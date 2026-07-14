"""Order execution wrapper: dry-run gate over any Broker adapter.

Market orders are used for both entries and exits because they are the lowest
common denominator across venues (and the only order type the live Trading 212
API accepts); protective stop/take-profit levels are managed by the bot in
software and converted to market exits when breached.
"""

from __future__ import annotations

import logging

from ..brokers.base import Broker, BrokerError

log = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self, broker: Broker, dry_run: bool = True):
        self.broker = broker
        self.dry_run = dry_run

    def _mode(self) -> str:
        if self.dry_run:
            return "DRY-RUN"
        return f"{self.broker.name}{':REAL' if self.broker.real_money else ':paper'}"

    def buy_market(self, symbol: str, quantity: float) -> dict | None:
        log.info("[%s] BUY %s x %s (market)", self._mode(), symbol, quantity)
        if self.dry_run:
            return {"simulated": True, "symbol": symbol, "quantity": quantity}
        try:
            return self.broker.buy_market(symbol, quantity)
        except BrokerError as exc:
            log.error("buy %s failed: %s", symbol, exc)
            return None

    def sell_market(self, symbol: str, quantity: float) -> dict | None:
        log.info("[%s] SELL %s x %s (market)", self._mode(), symbol, quantity)
        if self.dry_run:
            return {"simulated": True, "symbol": symbol, "quantity": -abs(quantity)}
        try:
            return self.broker.sell_market(symbol, abs(quantity))
        except BrokerError as exc:
            log.error("sell %s failed: %s", symbol, exc)
            return None
