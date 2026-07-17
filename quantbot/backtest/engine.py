"""Event-driven portfolio backtester.

Mirrors the live bot's decision logic so backtest results actually describe
what the bot would do:

  * Signals are computed on bar ``t-1``'s close and executed at bar ``t``'s
    open (no lookahead), with configurable slippage and fees.
  * Same risk model: ATR sizing, ATR stop/take-profit, trailing stop, max
    positions, exposure cap, cash buffer.
  * Intrabar exits: if the day's low pierces the stop we fill at the stop
    price (gap-through fills at the open, i.e. worse — realistic).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..indicators import atr as atr_indicator
from ..risk.manager import RiskConfig, RiskManager
from ..strategies.base import Strategy
from . import metrics


@dataclass
class BacktestSettings:
    initial_cash: float = 10_000.0
    slippage_bps: float = 5.0       # applied against you on every fill
    fee_bps: float = 2.0            # T212 is commission-free; approximates FX/spread costs
    min_entry_score: float = 0.25
    exit_score: float = -0.25
    atr_period: int = 14
    reentry_cooldown_bars: int = 2  # mirrors the live bot's whipsaw guard
    # Short selling: score <= -min_entry_score opens a short (fully
    # collateralized: the short's notional is reserved from cash, like the
    # live bot's buying-power accounting). With a regime series provided,
    # shorts only open on risk-off dates — mirroring the live direction gates.
    allow_short: bool = False


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    exit_reason: str
    direction: int = 1  # +1 long, -1 short


@dataclass
class _OpenPosition:
    quantity: float
    entry_price: float
    entry_cost: float  # cash actually reserved, including fees
    entry_date: pd.Timestamp
    entry_bar: int
    stop: float
    take_profit: float
    partial_target: float
    extreme_close: float  # highest close for longs, lowest for shorts
    atr_at_entry: float
    direction: int = 1
    partial_taken: bool = False


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade]
    stats: dict = field(default_factory=dict)

    def summary(self) -> str:
        s = self.stats
        lines = [
            "=" * 58,
            "BACKTEST RESULTS",
            "=" * 58,
            f"  Period               {s['start']} -> {s['end']}  ({s['bars']} bars)",
            f"  Initial equity       {s['initial_equity']:>12,.2f}",
            f"  Final equity         {s['final_equity']:>12,.2f}",
            f"  Total return         {s['total_return_pct']:>11.2f}%",
            f"  CAGR                 {s['cagr_pct']:>11.2f}%",
            f"  Sharpe ratio         {s['sharpe']:>12.2f}",
            f"  Sortino ratio        {s['sortino']:>12.2f}",
            f"  Max drawdown         {s['max_drawdown_pct']:>11.2f}%",
            f"  Trades               {s['trades']:>12d}",
            f"  Win rate             {s['win_rate_pct']:>11.2f}%",
            f"  Profit factor        {s['profit_factor']:>12.2f}",
            f"  Expectancy/trade     {s['expectancy']:>12.2f}",
            "=" * 58,
            "Past performance does not guarantee future results.",
        ]
        return "\n".join(lines)


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        risk_config: RiskConfig | None = None,
        settings: BacktestSettings | None = None,
    ):
        self.strategy = strategy
        self.risk = RiskManager(risk_config or RiskConfig())
        self.settings = settings or BacktestSettings()

    def run(
        self,
        data: dict[str, pd.DataFrame],
        regime_ok: pd.Series | None = None,
    ) -> BacktestResult:
        """Run the backtest.

        ``regime_ok``: optional boolean series (indexed by date). On dates
        where it is False, no NEW entries are made (exits keep working) —
        mirrors the live bot's market-regime filter. It is shifted by one bar
        internally so the regime decision uses yesterday's benchmark close.
        """
        if not data:
            raise ValueError("no data supplied to backtest")
        st = self.settings
        slip = st.slippage_bps / 10_000.0
        fee = st.fee_bps / 10_000.0

        # Precompute per-symbol signal (shifted: decided on t-1) and ATR.
        frames: dict[str, pd.DataFrame] = {}
        for symbol, df in data.items():
            if df.empty or len(df) < 5:
                continue
            f = df.copy()
            f["signal"] = self.strategy.signal_series(df).shift(1)
            f["atr"] = atr_indicator(df, st.atr_period).shift(1)
            frames[symbol] = f
        if not frames:
            raise ValueError("all supplied data frames were empty/too short")

        all_dates = sorted(set().union(*[set(f.index) for f in frames.values()]))

        if regime_ok is not None:
            regime = (
                regime_ok.astype(bool)
                .shift(1)                       # decided on yesterday's close
                .reindex(pd.DatetimeIndex(all_dates))
                .ffill()
                .fillna(True)                   # fail-open before data starts
            )
        else:
            regime = pd.Series(True, index=pd.DatetimeIndex(all_dates))

        cash = st.initial_cash
        open_positions: dict[str, _OpenPosition] = {}
        last_exit_bar: dict[str, int] = {}
        bar_i = 0
        trades: list[Trade] = []
        equity_points: list[tuple[pd.Timestamp, float]] = []

        def mark_value(pos: _OpenPosition, price: float) -> float:
            """Cash a position would return at ``price``: entry collateral
            plus direction-signed price move. Long reduces to qty*price."""
            entry_value = pos.entry_price * pos.quantity
            return entry_value + pos.direction * (price * pos.quantity - entry_value)

        def close_position(symbol: str, date: pd.Timestamp, price: float, reason: str):
            nonlocal cash
            pos = open_positions.pop(symbol)
            last_exit_bar[symbol] = bar_i
            # slippage moves against you: sell (long exit) lower, buy-to-cover higher
            fill = price * (1.0 - pos.direction * slip)
            exit_fee = fill * pos.quantity * fee
            returned = mark_value(pos, fill) - exit_fee
            cash += returned
            trades.append(
                Trade(
                    symbol=symbol,
                    entry_date=pos.entry_date,
                    exit_date=date,
                    entry_price=pos.entry_price,
                    exit_price=fill,
                    quantity=pos.quantity,
                    pnl=returned - pos.entry_cost,
                    exit_reason=reason,
                    direction=pos.direction,
                )
            )

        def take_partial(pos: _OpenPosition, symbol: str, date: pd.Timestamp, price: float):
            """Sell/cover a fraction at the first target; the rest keeps running."""
            nonlocal cash
            fraction = self.risk.config.partial_tp_fraction
            qty_out = pos.quantity * fraction
            fill = price * (1.0 - pos.direction * slip)
            entry_value_chunk = pos.entry_price * qty_out
            returned = (
                entry_value_chunk
                + pos.direction * (fill * qty_out - entry_value_chunk)
                - fill * qty_out * fee
            )
            cost_chunk = pos.entry_cost * fraction
            cash += returned
            trades.append(
                Trade(
                    symbol=symbol,
                    entry_date=pos.entry_date,
                    exit_date=date,
                    entry_price=pos.entry_price,
                    exit_price=fill,
                    quantity=qty_out,
                    pnl=returned - cost_chunk,
                    exit_reason="partial_tp",
                    direction=pos.direction,
                )
            )
            pos.quantity -= qty_out
            pos.entry_cost -= cost_chunk
            pos.partial_taken = True

        for bar_i, date in enumerate(all_dates):
            bars = {
                s: f.loc[date]
                for s, f in frames.items()
                if date in f.index and not np.isnan(f.loc[date, "Open"])
            }

            # ---- exits first (stop / take-profit / signal) ----
            for symbol in list(open_positions):
                if symbol not in bars:
                    continue
                bar = bars[symbol]
                pos = open_positions[symbol]
                d = pos.direction
                open_px, low, high, close = (
                    float(bar["Open"]), float(bar["Low"]),
                    float(bar["High"]), float(bar["Close"]),
                )
                # adverse extreme pierces the stop (long: low; short: high)
                if (open_px - pos.stop) * d <= 0:  # gapped through overnight
                    close_position(symbol, date, open_px, "stop_gap")
                    continue
                if ((low if d > 0 else high) - pos.stop) * d <= 0:
                    close_position(symbol, date, pos.stop, "stop_loss")
                    continue
                # first target: bank a fraction, keep the rest running
                if (
                    not pos.partial_taken
                    and self.risk.config.partial_tp_fraction > 0
                    and ((high if d > 0 else low) - pos.partial_target) * d >= 0
                ):
                    fill = (
                        max(open_px, pos.partial_target) if d > 0
                        else min(open_px, pos.partial_target)
                    )
                    take_partial(pos, symbol, date, fill)
                # favorable extreme reaches take-profit (long: high; short: low)
                if ((high if d > 0 else low) - pos.take_profit) * d >= 0:
                    fill = max(open_px, pos.take_profit) if d > 0 else min(open_px, pos.take_profit)
                    close_position(symbol, date, fill, "take_profit")
                    continue
                sig = bar["signal"]
                if not np.isnan(sig):
                    if d > 0 and sig <= st.exit_score:
                        close_position(symbol, date, open_px, "signal_exit")
                        continue
                    if d < 0 and sig >= -st.exit_score:
                        close_position(symbol, date, open_px, "signal_cover")
                        continue
                # stale position: capital is better deployed elsewhere
                max_hold = self.risk.config.max_holding_days
                if max_hold > 0 and bar_i - pos.entry_bar >= max_hold:
                    close_position(symbol, date, open_px, "time_exit")
                    continue
                # trailing + breakeven stop updates from today's close
                pos.extreme_close = (
                    max(pos.extreme_close, close) if d > 0 else min(pos.extreme_close, close)
                )
                pos.stop = self.risk.updated_trailing_stop(
                    pos.stop, pos.extreme_close, pos.atr_at_entry, d
                )
                pos.stop = self.risk.breakeven_stop(
                    pos.stop, pos.entry_price, pos.extreme_close, pos.atr_at_entry, d
                )

            # ---- mark to market ----
            def total_position_value() -> float:
                return sum(
                    mark_value(pos, float(bars[s]["Close"]) if s in bars else pos.entry_price)
                    for s, pos in open_positions.items()
                )

            equity = cash + total_position_value()

            # ---- entries: rank all qualifying candidates by |score| ----
            # risk-on regime: longs only; risk-off: shorts only (if enabled);
            # no regime series provided: both directions always allowed.
            risk_on = bool(regime.loc[date])
            longs_ok = risk_on
            shorts_ok = st.allow_short and (not risk_on or regime_ok is None)
            candidates = []
            for symbol, bar in bars.items():
                if symbol in open_positions:
                    continue
                if bar_i - last_exit_bar.get(symbol, -(10**9)) <= st.reentry_cooldown_bars:
                    continue
                sig, atr_val = bar["signal"], bar["atr"]
                if np.isnan(sig) or np.isnan(atr_val) or atr_val <= 0:
                    continue
                if longs_ok and sig >= st.min_entry_score:
                    candidates.append(
                        (abs(float(sig)), symbol, float(bar["Open"]), float(atr_val), 1)
                    )
                elif shorts_ok and sig <= -st.min_entry_score:
                    candidates.append(
                        (abs(float(sig)), symbol, float(bar["Open"]), float(atr_val), -1)
                    )
            candidates.sort(reverse=True)

            for _, symbol, open_px, atr_val, d in candidates:
                exposure = sum(
                    pos.entry_price * pos.quantity for pos in open_positions.values()
                )
                ok, _why = self.risk.can_open_new(len(open_positions), exposure, equity)
                if not ok:
                    break
                fill = open_px * (1.0 + d * slip)  # buy higher, sell-short lower
                qty = self.risk.position_size(equity, cash, fill, atr_val)
                if qty <= 0:
                    continue
                # both directions reserve collateral equal to notional + fee
                cost = fill * qty * (1.0 + fee)
                if cost > cash:
                    continue
                cash -= cost
                bar = bars[symbol]
                close = float(bar["Close"])
                open_positions[symbol] = _OpenPosition(
                    quantity=qty,
                    entry_price=fill,
                    entry_cost=cost,
                    entry_date=date,
                    entry_bar=bar_i,
                    stop=self.risk.stop_loss_price(fill, atr_val, d),
                    take_profit=self.risk.take_profit_price(fill, atr_val, d),
                    partial_target=self.risk.partial_tp_price(fill, atr_val, d),
                    extreme_close=max(fill, close) if d > 0 else min(fill, close),
                    atr_at_entry=atr_val,
                    direction=d,
                )

            # re-mark after entries
            equity_points.append((date, cash + total_position_value()))

        # liquidate remaining positions at final close for clean accounting
        final_date = all_dates[-1]
        for symbol in list(open_positions):
            f = frames[symbol]
            last_close = float(f["Close"].dropna().iloc[-1])
            close_position(symbol, final_date, last_close, "end_of_backtest")
        if equity_points:
            equity_points[-1] = (final_date, cash)

        equity_curve = pd.Series(
            [v for _, v in equity_points],
            index=pd.DatetimeIndex([d for d, _ in equity_points]),
            name="equity",
        )

        pnls = [t.pnl for t in trades]
        stats = {
            "start": str(equity_curve.index[0].date()),
            "end": str(equity_curve.index[-1].date()),
            "bars": len(equity_curve),
            "initial_equity": st.initial_cash,
            "final_equity": float(equity_curve.iloc[-1]),
            "total_return_pct": metrics.total_return_pct(equity_curve),
            "cagr_pct": metrics.cagr_pct(equity_curve),
            "sharpe": metrics.sharpe_ratio(equity_curve),
            "sortino": metrics.sortino_ratio(equity_curve),
            "max_drawdown_pct": metrics.max_drawdown_pct(equity_curve),
            **metrics.trade_stats(pnls),
        }
        return BacktestResult(equity_curve=equity_curve, trades=trades, stats=stats)
