"""Alpaca adapter (US stocks/ETFs, commission-free, first-class paper API).

Uses the plain REST API via requests — no SDK dependency. Keys come from the
``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY`` environment variables.
Docs: https://docs.alpaca.markets/reference
"""

from __future__ import annotations

import os

import requests

from .base import AccountSnapshot, Broker, BrokerError, BrokerPosition

BASE_URLS = {
    "paper": "https://paper-api.alpaca.markets",
    "live": "https://api.alpaca.markets",
}


class AlpacaBroker(Broker):
    name = "alpaca"
    supports_short = True  # sell-first shorts marginable US equities

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
        timeout: float = 30.0,
    ):
        key_id = key_id or os.environ.get("APCA_API_KEY_ID", "")
        secret_key = secret_key or os.environ.get("APCA_API_SECRET_KEY", "")
        if not key_id or not secret_key:
            raise BrokerError(
                "Alpaca credentials required: set APCA_API_KEY_ID and "
                "APCA_API_SECRET_KEY environment variables"
            )
        self.base_url = BASE_URLS["paper" if paper else "live"]
        self.real_money = not paper
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        )

    def _request(self, method: str, path: str, json_body: dict | None = None):
        try:
            resp = self._session.request(
                method, f"{self.base_url}{path}", json=json_body, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise BrokerError(f"alpaca network error: {exc}") from exc
        if resp.status_code >= 400:
            raise BrokerError(f"alpaca API error {resp.status_code}: {resp.text}")
        return resp.json() if resp.content else None

    def account(self) -> AccountSnapshot:
        acct = self._request("GET", "/v2/account")
        return AccountSnapshot(
            equity=float(acct.get("equity") or 0.0),
            cash=float(acct.get("cash") or 0.0),
            currency=str(acct.get("currency") or ""),
        )

    def positions(self) -> dict[str, BrokerPosition]:
        out = {}
        for p in self._request("GET", "/v2/positions") or []:
            symbol = str(p.get("symbol"))
            out[symbol] = BrokerPosition(
                symbol=symbol,
                quantity=float(p.get("qty") or 0.0),
                average_price=float(p.get("avg_entry_price") or 0.0),
                current_price=(
                    float(p["current_price"]) if p.get("current_price") else None
                ),
            )
        return out

    def _order(self, symbol: str, quantity: float, side: str) -> dict:
        return self._request(
            "POST",
            "/v2/orders",
            {
                "symbol": symbol,
                "qty": str(abs(quantity)),
                "side": side,
                "type": "market",
                "time_in_force": "day",
            },
        )

    def buy_market(self, symbol: str, quantity: float) -> dict:
        return self._order(symbol, quantity, "buy")

    def sell_market(self, symbol: str, quantity: float) -> dict:
        return self._order(symbol, quantity, "sell")
