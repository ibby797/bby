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
            exposure_value = sum(
                abs(p.quantity) * (p.current_price or p.average_price or 0.0)
                for p in portfolio.values()
            )
            self._scan_entries(equity, free_cash, exposure_value)

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
        # Negative broker quantity = an externally opened short.
        for symbol, pos in portfolio.items():
            if symbol in self.state.positions or symbol not in self.config.instruments:
                continue
            data_symbol = self.config.instruments[symbol]
            df = self.data.history(data_symbol)
            if len(df) < MIN_BARS:
                continue
            direction = 1 if pos.quantity >= 0 else -1
            atr_val = float(atr_indicator(df).iloc[-1])
            entry = pos.average_price or float(df["Close"].iloc[-1])
            self.state.positions[symbol] = ManagedPosition(
                ticker=symbol,
                yahoo_symbol=data_symbol,
                quantity=abs(pos.quantity),
                entry_price=entry,
                entry_time=dt.datetime.now(dt.timezone.utc).isoformat(),
                stop_price=self.risk.stop_loss_price(entry, atr_val, direction),
                take_profit_price=self.risk.take_profit_price(entry, atr_val, direction),
                highest_close=float(df["Close"].iloc[-1]),
                atr_at_entry=atr_val,
                direction=direction,
            )
            log.info("adopted external %s position %s with software stop %.2f",
                     "short" if direction < 0 else "long", symbol,
                     self.state.positions[symbol].stop_price)

        # Keep quantities in sync (partial manual sells etc.). If the sign
        # flipped externally, drop it — it gets re-adopted correctly next pass.
        for symbol in list(self.state.positions):
            mp = self.state.positions[symbol]
            broker_pos = portfolio.get(symbol)
            if broker_pos is None or abs(broker_pos.quantity) <= 1e-12:
                continue
            if (1 if broker_pos.quantity >= 0 else -1) != mp.direction:
                log.warning("position %s flipped direction outside the bot — resyncing", symbol)
                del self.state.positions[symbol]
                continue
            if abs(abs(broker_pos.quantity) - mp.quantity) > 1e-9:
                mp.quantity = abs(broker_pos.quantity)

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
            d = mp.direction
            df = self.data.history(mp.yahoo_symbol)
            if df.empty:
                continue
            price = float(df["Close"].iloc[-1])

            # first target: bank a fraction, keep the rest running
            fraction = self.config.risk.partial_tp_fraction
            if fraction > 0 and not mp.partial_taken:
                if mp.partial_tp_price <= 0:  # adopted/legacy position
                    mp.partial_tp_price = self.risk.partial_tp_price(
                        mp.entry_price, mp.atr_at_entry, d
                    )
                if (price - mp.partial_tp_price) * d >= 0:
                    qty_out = mp.quantity * fraction
                    result = (
                        self.executor.sell_market(symbol, qty_out) if d > 0
                        else self.executor.buy_market(symbol, qty_out)
                    )
                    if result is not None:
                        mp.quantity -= qty_out
                        mp.partial_taken = True
                        self.notifier.send(
                            f"PARTIAL {symbol} x{qty_out:g} @ ~{price:.2f} — "
                            "first target hit, rest rides the trailing stop"
                        )

            reason = None
            if force_liquidate:
                reason = "drawdown kill switch liquidation"
            elif (price - mp.stop_price) * d <= 0:  # long: price<=stop, short: price>=stop
                reason = f"stop-loss hit (price {price:.2f} vs stop {mp.stop_price:.2f})"
            elif (price - mp.take_profit_price) * d >= 0:
                reason = f"take-profit hit (price {price:.2f} vs tp {mp.take_profit_price:.2f})"
            elif self._held_too_long(mp):
                reason = f"time exit (> {self.config.risk.max_holding_days} days held)"
            else:
                sig = self.strategy.generate(df)
                # long closes on a sell signal; short covers on a buy signal
                if d > 0 and sig.score <= self.config.strategy.exit_score:
                    reason = f"sell signal ({sig.reason})"
                elif d < 0 and sig.score >= -self.config.strategy.exit_score:
                    reason = f"cover signal ({sig.reason})"

            if reason:
                broker_pos = portfolio.get(symbol)
                qty = abs(broker_pos.quantity) if broker_pos else mp.quantity
                if d > 0:
                    result = self.executor.sell_market(symbol, qty)
                else:
                    result = self.executor.buy_market(symbol, qty)  # cover short
                if result is not None:
                    pnl_pct = d * (price / mp.entry_price - 1.0) * 100.0
                    side = "EXIT" if d > 0 else "COVER"
                    self.notifier.send(
                        f"{side} {symbol} x{qty:g} @ ~{price:.2f} ({pnl_pct:+.2f}%) — {reason}"
                    )
                    del self.state.positions[symbol]
                    self.state.cooldowns[symbol] = dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat()
                continue

            # trailing + breakeven stop maintenance (extreme = highest close
            # for longs, lowest for shorts; stop only ever moves in our favor)
            mp.highest_close = (
                max(mp.highest_close, price) if d > 0 else min(mp.highest_close, price)
            )
            new_stop = self.risk.updated_trailing_stop(
                mp.stop_price, mp.highest_close, mp.atr_at_entry, d
            )
            new_stop = self.risk.breakeven_stop(
                new_stop, mp.entry_price, mp.highest_close, mp.atr_at_entry, d
            )
            if new_stop != mp.stop_price:
                log.info("%s stop %.2f -> %.2f", symbol, mp.stop_price, new_stop)
                mp.stop_price = new_stop

    def _held_too_long(self, mp: ManagedPosition) -> bool:
        max_days = self.config.risk.max_holding_days
        if max_days <= 0:
            return False
        try:
            entered = dt.datetime.fromisoformat(mp.entry_time)
        except ValueError:
            return False
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=dt.timezone.utc)
        return dt.datetime.now(dt.timezone.utc) - entered >= dt.timedelta(days=max_days)

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

    def regime_state(self) -> str:
        """'on' (benchmark above its SMA), 'off' (below), 'unknown' (no data),
        or 'disabled'. Fails open on missing data: a data outage should not
        silently change strategy behavior — capital protection is the kill
        switches' job."""
        r = self.config.regime
        if not r.enabled:
            return "disabled"
        df = self.data.history(r.symbol)
        if len(df) < r.sma_window:
            log.warning("regime filter: not enough %s data (%d bars) — fail-open",
                        r.symbol, len(df))
            return "unknown"
        benchmark_sma = float(sma(df["Close"], r.sma_window).iloc[-1])
        price = float(df["Close"].iloc[-1])
        return "on" if price >= benchmark_sma else "off"

    def regime_allows_entries(self) -> tuple[bool, str]:
        """Long-entry gate (kept for compatibility with regime_state)."""
        state = self.regime_state()
        r = self.config.regime
        if state == "off":
            return False, f"risk-off: {r.symbol} below SMA{r.sma_window}"
        if state == "unknown":
            return True, "insufficient regime data (fail-open)"
        return True, f"regime {state}"

    def _direction_gates(self) -> tuple[bool, bool, str]:
        """(longs_allowed, shorts_allowed, reason).

        Longs need a risk-on (or unknown/disabled) regime. Shorts need
        allow_short config + a broker that can short, and are blocked in
        risk-on regimes — shorting a market above its long-term average is a
        losing proposition often enough that the filter enforces alignment.
        """
        state = self.regime_state()
        longs = state in ("on", "unknown", "disabled")
        shorts_capable = (
            self.config.strategy.allow_short and self.broker.supports_short
        )
        shorts = shorts_capable and state in ("off", "unknown", "disabled")
        if self.config.strategy.allow_short and not self.broker.supports_short:
            log.info("allow_short is set but broker %s cannot short — longs only",
                     self.broker.name)
        return longs, shorts, f"regime={state}"

    def _scan_entries(
        self, equity: float, free_cash: float, exposure_value: float
    ) -> None:
        longs_ok, shorts_ok, gate_reason = self._direction_gates()
        if not longs_ok and not shorts_ok:
            log.info("entries blocked — %s", gate_reason)
            return

        min_score = self.config.strategy.min_entry_score
        now = dt.datetime.now(dt.timezone.utc)
        candidates: list[tuple[float, float, str, str, int]] = []
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
            if longs_ok and sig.score >= min_score:
                candidates.append((abs(sig.score), sig.score, symbol, data_symbol, 1))
                log.info("long candidate %s score=%.2f", symbol, sig.score)
            elif shorts_ok and sig.score <= -min_score:
                candidates.append((abs(sig.score), sig.score, symbol, data_symbol, -1))
                log.info("short candidate %s score=%.2f", symbol, sig.score)
        candidates.sort(reverse=True)

        for _, score, symbol, data_symbol, direction in candidates:
            ok, why = self.risk.can_open_new(
                len(self.state.positions), exposure_value, equity
            )
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

            if direction > 0:
                result = self.executor.buy_market(symbol, qty)
            else:
                result = self.executor.sell_market(symbol, qty)  # open short
            if result is None:
                continue
            stop = self.risk.stop_loss_price(price, atr_val, direction)
            tp = self.risk.take_profit_price(price, atr_val, direction)
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
                direction=direction,
                partial_tp_price=self.risk.partial_tp_price(price, atr_val, direction),
            )
            # both directions consume buying power / collateral
            free_cash -= qty * price
            exposure_value += qty * price
            side = "ENTRY" if direction > 0 else "SHORT"
            self.notifier.send(
                f"{side} {symbol} x{qty:g} @ ~{price:.2f} score={score:.2f} "
                f"stop={stop:.2f} tp={tp:.2f}"
            )
