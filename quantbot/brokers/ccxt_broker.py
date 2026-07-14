"""CCXT adapter: spot trading on 100+ crypto exchanges (Binance, Kraken,
Coinbase, Bybit, OKX...).

``ccxt`` is an optional dependency: ``pip install ccxt``. Universe symbols use
CCXT market notation (``BTC/USDT``) mapped to Yahoo data symbols (``BTC-USD``).
Keys come from ``CCXT_API_KEY`` / ``CCXT_SECRET`` env vars unless passed in.

CRYPTO IS REAL MONEY AND EXTREMELY VOLATILE. This adapter reports
``real_money = True`` unless the exchange's sandbox mode is enabled, so the
config confirmation gate applies. Set ``schedule.market_hours_only: false``
for 24/7 markets.
"""

from __future__ import annotations

import os

from .base import AccountSnapshot, Broker, BrokerError, BrokerPosition


class CcxtBroker(Broker):
    name = "ccxt"

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str | None = None,
        secret: str | None = None,
        quote_currency: str = "USDT",
        sandbox: bool = False,
    ):
        try:
            import ccxt
        except ImportError as exc:
            raise BrokerError(
                "ccxt is not installed — run: pip install ccxt"
            ) from exc
        api_key = api_key or os.environ.get("CCXT_API_KEY", "")
        secret = secret or os.environ.get("CCXT_SECRET", "")
        if not api_key or not secret:
            raise BrokerError(
                "CCXT credentials required: set CCXT_API_KEY and CCXT_SECRET"
            )
        try:
            exchange_cls = getattr(ccxt, exchange_id)
        except AttributeError as exc:
            raise BrokerError(f"unknown ccxt exchange: {exchange_id}") from exc
        self.exchange = exchange_cls(
            {"apiKey": api_key, "secret": secret, "enableRateLimit": True}
        )
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self.real_money = not sandbox
        self.quote = quote_currency.upper()
        self.name = f"ccxt:{exchange_id}"

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # ccxt raises many exchange-specific types
            raise BrokerError(f"{self.name}: {exc}") from exc

    def _balances(self) -> dict:
        return self._call(self.exchange.fetch_balance)

    def account(self) -> AccountSnapshot:
        bal = self._balances()
        free_quote = float((bal.get("free") or {}).get(self.quote) or 0.0)
        equity = float((bal.get("total") or {}).get(self.quote) or 0.0)
        for asset, total in (bal.get("total") or {}).items():
            if asset == self.quote or not total:
                continue
            try:
                ticker = self._call(self.exchange.fetch_ticker, f"{asset}/{self.quote}")
                equity += float(total) * float(ticker["last"])
            except BrokerError:
                continue  # unpriceable dust
        return AccountSnapshot(equity=equity, cash=free_quote, currency=self.quote)

    def positions(self) -> dict[str, BrokerPosition]:
        """Spot 'positions' = non-quote balances, keyed as CCXT market symbols."""
        bal = self._balances()
        out = {}
        for asset, total in (bal.get("total") or {}).items():
            if asset == self.quote or not total or float(total) <= 0:
                continue
            symbol = f"{asset}/{self.quote}"
            try:
                last = float(self._call(self.exchange.fetch_ticker, symbol)["last"])
            except BrokerError:
                continue
            if float(total) * last < 1.0:  # ignore sub-1-quote-unit dust
                continue
            out[symbol] = BrokerPosition(
                symbol=symbol,
                quantity=float(total),
                average_price=last,  # spot exchanges don't track entry price
                current_price=last,
            )
        return out

    def buy_market(self, symbol: str, quantity: float) -> dict:
        return self._call(self.exchange.create_market_buy_order, symbol, abs(quantity))

    def sell_market(self, symbol: str, quantity: float) -> dict:
        return self._call(self.exchange.create_market_sell_order, symbol, abs(quantity))
