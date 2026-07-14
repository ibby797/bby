"""Trading 212 adapter (wraps the REST client in quantbot.api.client)."""

from __future__ import annotations

from ..api.client import Trading212APIError, Trading212Client
from .base import AccountSnapshot, Broker, BrokerError, BrokerPosition


class Trading212Broker(Broker):
    name = "trading212"

    def __init__(self, api_key: str, environment: str = "demo"):
        self.client = Trading212Client(api_key, environment)
        self.real_money = environment == "live"

    def account(self) -> AccountSnapshot:
        try:
            cash = self.client.get_account_cash()
        except Trading212APIError as exc:
            raise BrokerError(str(exc)) from exc
        return AccountSnapshot(
            equity=float(cash.get("total") or 0.0),
            cash=float(cash.get("free") or 0.0),
        )

    def positions(self) -> dict[str, BrokerPosition]:
        try:
            portfolio = self.client.get_portfolio()
        except Trading212APIError as exc:
            raise BrokerError(str(exc)) from exc
        out = {}
        for p in portfolio:
            symbol = str(p.get("ticker"))
            out[symbol] = BrokerPosition(
                symbol=symbol,
                quantity=float(p.get("quantity") or 0.0),
                average_price=float(p.get("averagePrice") or 0.0),
                current_price=(
                    float(p["currentPrice"]) if p.get("currentPrice") is not None else None
                ),
            )
        return out

    def buy_market(self, symbol: str, quantity: float) -> dict:
        try:
            return self.client.place_market_order(symbol, abs(quantity))
        except Trading212APIError as exc:
            raise BrokerError(str(exc)) from exc

    def sell_market(self, symbol: str, quantity: float) -> dict:
        try:
            return self.client.place_market_order(symbol, -abs(quantity))
        except Trading212APIError as exc:
            raise BrokerError(str(exc)) from exc
