"""Trading 212 public API client (v0).

Docs: https://docs.trading212.com/api

Key facts encoded here:
  * Two environments — demo (paper) and live — with identical endpoints:
      demo: https://demo.trading212.com/api/v0
      live: https://live.trading212.com/api/v0
  * Auth is the raw API key in the ``Authorization`` header.
  * Each endpoint family has its own rate limit; we enforce a conservative
    client-side minimum interval per endpoint and also honor 429 responses.
  * The API is order/portfolio only — it does NOT stream market prices, which
    is why market data comes from a separate provider.
  * On the LIVE environment only MARKET orders are accepted; limit/stop/
    stop-limit orders work on demo. Protective stops on live must therefore be
    enforced in software (the bot does this).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URLS = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}

# Conservative client-side minimum seconds between calls, per endpoint family.
# Slightly slower than the documented limits so we never trip 429s in practice.
_MIN_INTERVALS = {
    "account_info": 30.0,
    "account_cash": 2.0,
    "portfolio": 5.0,
    "position": 1.0,
    "orders_list": 5.0,
    "order_place": 2.0,
    "order_get": 1.0,
    "order_cancel": 1.0,
    "instruments": 60.0,
    "exchanges": 60.0,
    "history": 6.0,
}


class Trading212APIError(Exception):
    def __init__(self, status_code: int, message: str, payload: Any = None):
        super().__init__(f"Trading 212 API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


class _RateLimiter:
    """Per-key minimum-interval limiter, thread-safe."""

    def __init__(self, min_intervals: dict[str, float]):
        self._min_intervals = min_intervals
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> None:
        min_interval = self._min_intervals.get(key, 1.0)
        with self._lock:
            now = time.monotonic()
            wait_for = self._last_call.get(key, -min_interval) + min_interval - now
            if wait_for > 0:
                # Reserve the slot before sleeping so concurrent callers queue.
                self._last_call[key] = now + wait_for
            else:
                self._last_call[key] = now
                wait_for = 0.0
        if wait_for > 0:
            time.sleep(wait_for)


class Trading212Client:
    def __init__(
        self,
        api_key: str,
        environment: str = "demo",
        timeout: float = 30.0,
        max_retries: int = 4,
        session: requests.Session | None = None,
    ):
        if environment not in BASE_URLS:
            raise ValueError(f"environment must be one of {sorted(BASE_URLS)}")
        if not api_key:
            raise ValueError(
                "Trading 212 API key is required (set T212_API_KEY or config api.key). "
                "Generate one in the Trading 212 app: Settings -> API (Beta)."
            )
        self.environment = environment
        self.base_url = BASE_URLS[environment]
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = session or requests.Session()
        self._session.headers.update(
            {"Authorization": api_key, "Content-Type": "application/json"}
        )
        self._limiter = _RateLimiter(_MIN_INTERVALS)

    # ------------------------------------------------------------------ core

    def _request(
        self,
        method: str,
        path: str,
        rate_key: str,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        backoff = 2.0
        for attempt in range(self.max_retries + 1):
            self._limiter.wait(rate_key)
            try:
                resp = self._session.request(
                    method, url, json=json_body, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise Trading212APIError(0, f"network error: {exc}") from exc
                log.warning("network error on %s %s (%s), retrying", method, path, exc)
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff))
                if attempt >= self.max_retries:
                    raise Trading212APIError(429, "rate limited", resp.text)
                log.warning("rate limited on %s, sleeping %.1fs", path, retry_after)
                time.sleep(retry_after)
                backoff *= 2
                continue

            if resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise Trading212APIError(resp.status_code, "server error", resp.text)
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = resp.text
                message = payload if isinstance(payload, str) else (
                    payload.get("errorMessage")
                    or payload.get("message")
                    or payload.get("code")
                    or str(payload)
                )
                raise Trading212APIError(resp.status_code, str(message), payload)

            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        raise Trading212APIError(0, "retries exhausted")  # pragma: no cover

    # --------------------------------------------------------------- account

    def get_account_info(self) -> dict:
        """Account id and base currency."""
        return self._request("GET", "/equity/account/info", "account_info")

    def get_account_cash(self) -> dict:
        """Cash breakdown: free, invested, result (unrealized), total, blocked..."""
        return self._request("GET", "/equity/account/cash", "account_cash")

    # ------------------------------------------------------------- portfolio

    def get_portfolio(self) -> list[dict]:
        """All open positions."""
        return self._request("GET", "/equity/portfolio", "portfolio") or []

    def get_position(self, ticker: str) -> dict:
        return self._request("GET", f"/equity/portfolio/{ticker}", "position")

    # -------------------------------------------------------------- metadata

    def get_instruments(self) -> list[dict]:
        """Full tradable instrument list (ticker, name, minTradeQuantity...)."""
        return self._request("GET", "/equity/metadata/instruments", "instruments") or []

    def get_exchanges(self) -> list[dict]:
        return self._request("GET", "/equity/metadata/exchanges", "exchanges") or []

    # ---------------------------------------------------------------- orders

    def get_pending_orders(self) -> list[dict]:
        return self._request("GET", "/equity/orders", "orders_list") or []

    def get_order(self, order_id: int) -> dict:
        return self._request("GET", f"/equity/orders/{order_id}", "order_get")

    def place_market_order(self, ticker: str, quantity: float) -> dict:
        """Positive quantity buys, negative sells. The only order type accepted
        on the live environment."""
        body = {"ticker": ticker, "quantity": quantity}
        return self._request("POST", "/equity/orders/market", "order_place", body)

    def place_limit_order(
        self, ticker: str, quantity: float, limit_price: float, time_validity: str = "DAY"
    ) -> dict:
        body = {
            "ticker": ticker,
            "quantity": quantity,
            "limitPrice": limit_price,
            "timeValidity": time_validity,
        }
        return self._request("POST", "/equity/orders/limit", "order_place", body)

    def place_stop_order(
        self, ticker: str, quantity: float, stop_price: float, time_validity: str = "DAY"
    ) -> dict:
        body = {
            "ticker": ticker,
            "quantity": quantity,
            "stopPrice": stop_price,
            "timeValidity": time_validity,
        }
        return self._request("POST", "/equity/orders/stop", "order_place", body)

    def place_stop_limit_order(
        self,
        ticker: str,
        quantity: float,
        stop_price: float,
        limit_price: float,
        time_validity: str = "DAY",
    ) -> dict:
        body = {
            "ticker": ticker,
            "quantity": quantity,
            "stopPrice": stop_price,
            "limitPrice": limit_price,
            "timeValidity": time_validity,
        }
        return self._request("POST", "/equity/orders/stop_limit", "order_place", body)

    def cancel_order(self, order_id: int) -> None:
        self._request("DELETE", f"/equity/orders/{order_id}", "order_cancel")

    # --------------------------------------------------------------- history

    def get_order_history(self, cursor: int | None = None, limit: int = 50) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._request("GET", "/equity/history/orders", "history", params=params)

    def get_dividends(self, cursor: int | None = None, limit: int = 50) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._request("GET", "/history/dividends", "history", params=params)

    def get_transactions(self, cursor: str | None = None, limit: int = 50) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._request("GET", "/history/transactions", "history", params=params)
