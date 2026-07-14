"""Risk management: the part of a trading system that actually keeps you alive.

Layers, from per-trade to account-wide:

  1. Volatility-adjusted position sizing — risk a fixed fraction of equity per
     trade, with the stop distance derived from ATR, so turbulent instruments
     automatically get smaller positions.
  2. Hard caps — max position size, max open positions, max total exposure,
     minimum cash buffer.
  3. Protective exits — ATR stop-loss, ATR take-profit, optional trailing stop
     that ratchets up with new highs but never down.
  4. Kill switches — max daily loss and max drawdown from peak equity halt all
     new entries (drawdown breach also flags existing positions for exit).

No sizing scheme can prevent losses; these controls bound them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0        # % of equity risked entry -> stop
    max_position_pct: float = 20.0         # max % of equity per position
    max_total_exposure_pct: float = 90.0   # max % of equity invested overall
    max_open_positions: int = 5
    min_cash_buffer_pct: float = 5.0       # always keep this % of equity in cash
    atr_stop_multiplier: float = 2.5
    atr_take_profit_multiplier: float = 5.0
    trailing_stop: bool = True
    max_daily_loss_pct: float = 3.0        # halt entries for the day
    max_drawdown_pct: float = 15.0         # global halt + liquidation flag
    quantity_decimals: int = 4             # T212 supports fractional shares
    reentry_cooldown_hours: float = 24.0   # whipsaw guard after any exit

    def validate(self) -> None:
        if not 0 < self.risk_per_trade_pct <= 10:
            raise ValueError("risk_per_trade_pct must be in (0, 10]")
        if not 0 < self.max_position_pct <= 100:
            raise ValueError("max_position_pct must be in (0, 100]")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be >= 1")
        if self.atr_stop_multiplier <= 0:
            raise ValueError("atr_stop_multiplier must be > 0")
        if self.reentry_cooldown_hours < 0:
            raise ValueError("reentry_cooldown_hours must be >= 0")


@dataclass(frozen=True)
class KillSwitchStatus:
    halted: bool
    liquidate: bool
    reason: str


class RiskManager:
    def __init__(self, config: RiskConfig):
        config.validate()
        self.config = config

    # ------------------------------------------------------------- sizing

    def position_size(
        self, equity: float, free_cash: float, price: float, atr_value: float
    ) -> float:
        """Quantity to buy, or 0.0 if no valid size exists.

        Sized so that (entry - stop) * quantity = risk_per_trade_pct of equity,
        then capped by max position value, spendable cash, and rounded down to
        the configured fractional precision.
        """
        if price <= 0 or equity <= 0:
            return 0.0
        if atr_value is None or not math.isfinite(atr_value) or atr_value <= 0:
            return 0.0

        risk_amount = equity * self.config.risk_per_trade_pct / 100.0
        stop_distance = atr_value * self.config.atr_stop_multiplier
        qty_by_risk = risk_amount / stop_distance

        max_value = equity * self.config.max_position_pct / 100.0
        qty_by_value = max_value / price

        cash_buffer = equity * self.config.min_cash_buffer_pct / 100.0
        spendable = free_cash - cash_buffer
        if spendable <= 0:
            return 0.0
        qty_by_cash = spendable / price

        qty = min(qty_by_risk, qty_by_value, qty_by_cash)
        factor = 10 ** self.config.quantity_decimals
        qty = math.floor(qty * factor) / factor
        return max(qty, 0.0)

    # ------------------------------------------------------------ exits

    def stop_loss_price(self, entry_price: float, atr_value: float) -> float:
        return entry_price - self.config.atr_stop_multiplier * atr_value

    def take_profit_price(self, entry_price: float, atr_value: float) -> float:
        return entry_price + self.config.atr_take_profit_multiplier * atr_value

    def updated_trailing_stop(
        self, current_stop: float, highest_close: float, atr_value: float
    ) -> float:
        """Trailing stop ratchets upward only."""
        if not self.config.trailing_stop:
            return current_stop
        candidate = highest_close - self.config.atr_stop_multiplier * atr_value
        return max(current_stop, candidate)

    # ------------------------------------------------------ kill switches

    def check_kill_switches(
        self, equity: float, peak_equity: float, day_start_equity: float
    ) -> KillSwitchStatus:
        if day_start_equity > 0:
            daily_loss_pct = (day_start_equity - equity) / day_start_equity * 100.0
            if daily_loss_pct >= self.config.max_daily_loss_pct:
                return KillSwitchStatus(
                    True,
                    False,
                    f"daily loss {daily_loss_pct:.2f}% >= limit "
                    f"{self.config.max_daily_loss_pct:.2f}% — no new entries today",
                )
        if peak_equity > 0:
            drawdown_pct = (peak_equity - equity) / peak_equity * 100.0
            if drawdown_pct >= self.config.max_drawdown_pct:
                return KillSwitchStatus(
                    True,
                    True,
                    f"drawdown {drawdown_pct:.2f}% >= limit "
                    f"{self.config.max_drawdown_pct:.2f}% — halting and flagging liquidation",
                )
        return KillSwitchStatus(False, False, "")

    # ----------------------------------------------------- entry gating

    def can_open_new(
        self, open_positions: int, exposure_value: float, equity: float
    ) -> tuple[bool, str]:
        if open_positions >= self.config.max_open_positions:
            return False, f"max open positions reached ({self.config.max_open_positions})"
        if equity > 0:
            exposure_pct = exposure_value / equity * 100.0
            if exposure_pct >= self.config.max_total_exposure_pct:
                return False, (
                    f"exposure {exposure_pct:.1f}% >= limit "
                    f"{self.config.max_total_exposure_pct:.1f}%"
                )
        return True, ""
