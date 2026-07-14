"""The live trading orchestrator — broker-agnostic.

Each cycle:
  1. Sync account (cash/equity) and positions from the configured broker
     (Trading 212, Alpaca, a CCXT crypto exchange, or the built-in paper
     broker).
  2. Reconcile bot state with the actual portfolio (adopt or drop positions
     changed outside the bot).
  3. Roll the daily ledger, update peak equity, evaluate kill switches.
  4. Manage exits: software stop-loss / take-profit / trailing stop / ensemble
     sell signal -> market sell.
  5. Check the market-regime filter, then scan for entries: rank the universe
     by ensemble score, size by risk, buy.
  6. Persist state.

Decisions run on the latest completed OHLCV bars from the data provider. With
daily bars, stops are evaluated once per poll against the latest price — an
approximation of a true exchange-side stop that is documented in the README.
"""

from __future__ import annotations

import datetime as dt
import logging
import signal
import time

from .brokers import build_broker
from .brokers.base import BrokerError, BrokerPosition
from .config import Config
from .data.market_data import MarketDataProvider
from .execution.executor import OrderExecutor
from .indicators import atr as atr_indicator, sma
from .notify import Notifier
from .risk.manager import RiskManager
from .state import BotState, ManagedPosition, load_state, save_state
from .strategies import build_default_ensemble

log = logging.getLogger(__name__)

MIN_BARS = 60  # minimum history required before an instrument is tradable


def us_market_open_now(now: dt.datetime | None = None) -> bool:
    """Approximate US regular session in UTC (14:30-21:00, Mon-Fri).

    Ignores DST edge weeks and exchange holidays — a coarse politeness filter.
    Disable via ``schedule.market_hours_only: false`` for 24/7 markets.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 14 * 60 + 30 <= minutes < 21 * 60


class TradingBot:
    def __init__(self, config: Config):
        self.config = config
        self.data = MarketDataProvider(
            interval=config.data.interval,
            lookback_days=config.data.lookback_days,
            cache_ttl_seconds=config.data.cache_ttl_seconds,
        )
        # Late-bound lambda so tests can swap self.data; maps broker symbol ->
        # data symbol for the paper broker's fills.
        self.broker = build_broker(
            config,
            price_lookup=lambda s: self.data.latest_price(
                self.config.instruments.get(s, s)
            ),
        )
        self.executor = OrderExecutor(self.broker, dry_run=config.dry_run)
        self.strategy = build_default_ensemble(config.strategy.weights)
        self.risk = RiskManager(config.risk)
        self.state: BotState = load_state(config.state_path)
        self.notifier = Notifier(config.webhook_url)
        self._stop_requested = False

    # ------------------------------------------------------------ lifecycle

    def request_stop(self, *_args) -> None:
        log.info("stop requested — finishing current cycle")
        self._stop_requested = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        mode = "DRY-RUN" if self.config.dry_run else self.broker.name
        self.notifier.send(
            f"quantbot started [{mode}] universe={len(self.config.instruments)} "
            f"poll={self.config.schedule.poll_seconds}s"
        )
        while not self._stop_requested:
            try:
                if self.config.schedule.market_hours_only and not us_market_open_now():
                    log.info("market closed — skipping cycle")
                else:
                    self.run_once()
            except BrokerError as exc:
                log.error("broker error during cycle: %s", exc)
            except Exception:
                log.exception("unexpected error during cycle")
            for _ in range(self.config.schedule.poll_seconds):
                if self._stop_requested:
                    break
                time.sleep(1)
        save_state(self.state, self.config.state_path)
        self.notifier.send("quantbot stopped")

    # ----------------------------------------------------------- one cycle

    def run_once(self) -> None:
        snapshot = self.broker.account()
        equity = snapshot.equity
        free_cash = snapshot.cash
        portfolio = self.broker.positions()

        self._reconcile(portfolio)
        self._roll_daily_ledger(equity)
        self.state.peak_equity = max(self.state.peak_equity, equity)

        ks = self.risk.check_kill_switches(
            equity, self.state.peak_equity, self.state.day_start_equity
        )
        if ks.halted and not self.state.halted:
            self.state.halted = True
            self.state.halt_reason = ks.reason
            self.notifier.send(f"KILL SWITCH: {ks.reason}")
        elif not ks.halted and self.state.halted and "daily loss" in self.state.halt_reason:
            # daily halt resets with the daily ledger roll
            self.state.halted = False
            self.state.halt_reason = ""

        log.info(
            "cycle[%s]: equity=%.2f free=%.2f positions=%d halted=%s",
            self.broker.name, equity, free_cash, len(self.state.positions),
            self.state.halted,
        )

        self._manage_exits(portfolio, force_liquidate=ks.liquidate)

        if not self.state.halted and not ks.halted:
            self._scan_entries(equity, free_cash)

        save_state(self.state, self.config.state_path)

    # -------------------------------------------------------- reconciliation

    def _reconcile(self, portfolio: dict[str, BrokerPosition]) -> None:
        """Align bot state with the broker's actual portfolio."""
        # Drop state positions that no longer exist at the broker.
        for symbol in list(self.state.positions):
            if symbol not in portfolio:
                log.warning("position %s closed outside the bot — dropping from state", symbol)
                del self.state.positions[symbol]

        # Adopt broker positions the bot doesn't know (manual buys, restarts).
        for symbol, pos in portfolio.items():
            if symbol in self.state.positions or symbol not in self.config.instruments:
                continue
            data_symbol = self.config.instruments[symbol]
            df = self.data.history(data_symbol)
            if len(df) < MIN_BARS:
                continue
            atr_val = float(atr_indicator(df).iloc[-1])
            entry = pos.average_price or float(df["Close"].iloc[-1])
            self.state.positions[symbol] = ManagedPosition(
                ticker=symbol,
                yahoo_symbol=data_symbol,
                quantity=pos.quantity,
                entry_price=entry,
                entry_time=dt.datetime.now(dt.timezone.utc).isoformat(),
                stop_price=self.risk.stop_loss_price(entry, atr_val),
                take_profit_price=self.risk.take_profit_price(entry, atr_val),
                highest_close=float(df["Close"].iloc[-1]),
                atr_at_entry=atr_val,
            )
            log.info("adopted external position %s with software stop %.2f",
                     symbol, self.state.positions[symbol].stop_price)

        # Keep quantities in sync (partial manual sells etc.).
        for symbol, mp in self.state.positions.items():
            broker_pos = portfolio.get(symbol)
            if broker_pos and broker_pos.quantity > 0 and abs(
                broker_pos.quantity - mp.quantity
            ) > 1e-9:
                mp.quantity = broker_pos.quantity

    def _roll_daily_ledger(self, equity: float) -> None:
        today = dt.date.today().isoformat()
        if self.state.day_date != today:
            self.state.day_date = today
            self.state.day_start_equity = equity
            if self.state.halted and "daily loss" in self.state.halt_reason:
                self.state.halted = False
                self.state.halt_reason = ""

    # ----------------------------------------------------------- exits

    def _manage_exits(
        self, portfolio: dict[str, BrokerPosition], force_liquidate: bool = False
    ) -> None:
        for symbol in list(self.state.positions):
            mp = self.state.positions[symbol]
            df = self.data.history(mp.yahoo_symbol)
            if df.empty:
                continue
            price = float(df["Close"].iloc[-1])

            reason = None
            if force_liquidate:
                reason = "drawdown kill switch liquidation"
            elif price <= mp.stop_price:
                reason = f"stop-loss hit ({price:.2f} <= {mp.stop_price:.2f})"
            elif price >= mp.take_profit_price:
                reason = f"take-profit hit ({price:.2f} >= {mp.take_profit_price:.2f})"
            else:
                sig = self.strategy.generate(df)
                if sig.score <= self.config.strategy.exit_score:
                    reason = f"sell signal ({sig.reason})"

            if reason:
                broker_pos = portfolio.get(symbol)
                qty = broker_pos.quantity if broker_pos else mp.quantity
                result = self.executor.sell_market(symbol, qty)
                if result is not None:
                    pnl_pct = (price / mp.entry_price - 1.0) * 100.0
                    self.notifier.send(
                        f"EXIT {symbol} x{qty:g} @ ~{price:.2f} ({pnl_pct:+.2f}%) — {reason}"
                    )
                    del self.state.positions[symbol]
                    self.state.cooldowns[symbol] = dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat()
                continue

            # trailing stop maintenance
            mp.highest_close = max(mp.highest_close, price)
            new_stop = self.risk.updated_trailing_stop(
                mp.stop_price, mp.highest_close, mp.atr_at_entry
            )
            if new_stop > mp.stop_price:
                log.info("%s trailing stop %.2f -> %.2f", symbol, mp.stop_price, new_stop)
                mp.stop_price = new_stop

    # ---------------------------------------------------------- entries

    def _in_cooldown(self, symbol: str, now: dt.datetime) -> bool:
        last_exit = self.state.cooldowns.get(symbol)
        if not last_exit:
            return False
        cooldown = dt.timedelta(hours=self.config.risk.reentry_cooldown_hours)
        if now - dt.datetime.fromisoformat(last_exit) >= cooldown:
            del self.state.cooldowns[symbol]  # expired — tidy up
            return False
        return True

    def regime_allows_entries(self) -> tuple[bool, str]:
        """Benchmark-above-its-SMA regime filter. Fails open on missing data:
        a data outage should not silently change strategy behavior — capital
        protection is the kill switches' job."""
        r = self.config.regime
        if not r.enabled:
            return True, "regime filter disabled"
        df = self.data.history(r.symbol)
        if len(df) < r.sma_window:
            log.warning("regime filter: not enough %s data (%d bars) — allowing entries",
                        r.symbol, len(df))
            return True, "insufficient regime data (fail-open)"
        benchmark_sma = float(sma(df["Close"], r.sma_window).iloc[-1])
        price = float(df["Close"].iloc[-1])
        if price < benchmark_sma:
            return False, (
                f"risk-off: {r.symbol} {price:.2f} < SMA{r.sma_window} {benchmark_sma:.2f}"
            )
        return True, f"risk-on: {r.symbol} above SMA{r.sma_window}"

    def _scan_entries(self, equity: float, free_cash: float) -> None:
        allowed, regime_reason = self.regime_allows_entries()
        if not allowed:
            log.info("entries blocked — %s", regime_reason)
            return

        now = dt.datetime.now(dt.timezone.utc)
        candidates: list[tuple[float, str, str]] = []
        for symbol, data_symbol in self.config.instruments.items():
            if symbol in self.state.positions:
                continue
            if self._in_cooldown(symbol, now):
                log.info("skipping %s: re-entry cooldown active", symbol)
                continue
            df = self.data.history(data_symbol)
            if len(df) < MIN_BARS:
                continue
            sig = self.strategy.generate(df)
            if sig.score >= self.config.strategy.min_entry_score:
                candidates.append((sig.score, symbol, data_symbol))
                log.info("candidate %s score=%.2f", symbol, sig.score)
        candidates.sort(reverse=True)

        for score, symbol, data_symbol in candidates:
            exposure = max(equity - free_cash, 0.0)
            ok, why = self.risk.can_open_new(len(self.state.positions), exposure, equity)
            if not ok:
                log.info("entry gate closed: %s", why)
                break

            df = self.data.history(data_symbol)
            price = float(df["Close"].iloc[-1])
            atr_val = float(atr_indicator(df).iloc[-1])
            qty = self.risk.position_size(equity, free_cash, price, atr_val)
            if qty <= 0:
                log.info("no valid size for %s (price=%.2f atr=%.2f)", symbol, price, atr_val)
                continue

            result = self.executor.buy_market(symbol, qty)
            if result is None:
                continue
            stop = self.risk.stop_loss_price(price, atr_val)
            tp = self.risk.take_profit_price(price, atr_val)
            self.state.positions[symbol] = ManagedPosition(
                ticker=symbol,
                yahoo_symbol=data_symbol,
                quantity=qty,
                entry_price=price,
                entry_time=dt.datetime.now(dt.timezone.utc).isoformat(),
                stop_price=stop,
                take_profit_price=tp,
                highest_close=price,
                atr_at_entry=atr_val,
            )
            free_cash -= qty * price
            self.notifier.send(
                f"ENTRY {symbol} x{qty:g} @ ~{price:.2f} score={score:.2f} "
                f"stop={stop:.2f} tp={tp:.2f}"
            )
