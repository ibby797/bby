"""Order execution wrapper: dry-run gate + market orders.

Market orders are used for both entries and exits because the live Trading 212
API accepts only market orders; protective stop/take-profit levels are managed
by the bot in software and converted to market exits when breached.
"""

from __future__ import annotations

import logging

from ..api.client import Trading212APIError, Trading212Client

log = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self, client: Trading212Client, dry_run: bool = True):
        self.client = client
        self.dry_run = dry_run

    def _mode(self) -> str:
        return "DRY-RUN" if self.dry_run else self.client.environment.upper()

    def buy_market(self, ticker: str, quantity: float) -> dict | None:
        log.info("[%s] BUY %s x %s (market)", self._mode(), ticker, quantity)
        if self.dry_run:
            return {"simulated": True, "ticker": ticker, "quantity": quantity}
        try:
            return self.client.place_market_order(ticker, quantity)
        except Trading212APIError as exc:
            log.error("buy %s failed: %s", ticker, exc)
            return None

    def sell_market(self, ticker: str, quantity: float) -> dict | None:
        log.info("[%s] SELL %s x %s (market)", self._mode(), ticker, quantity)
        if self.dry_run:
            return {"simulated": True, "ticker": ticker, "quantity": -quantity}
        try:
            return self.client.place_market_order(ticker, -abs(quantity))
        except Trading212APIError as exc:
            log.error("sell %s failed: %s", ticker, exc)
            return None
