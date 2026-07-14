"""Built-in paper broker: a fully local simulated account.

Needs no API keys and no network beyond market data, so the bot can run
anywhere immediately. Fills at the latest known data price, persists its
ledger (cash + positions) to a JSON file, and survives restarts.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from .base import AccountSnapshot, Broker, BrokerError, BrokerPosition

PriceLookup = Callable[[str], "float | None"]


class PaperBroker(Broker):
    name = "paper"
    real_money = False

    def __init__(
        self,
        price_lookup: PriceLookup,
        initial_cash: float = 10_000.0,
        ledger_path: str | Path = "paper_ledger.json",
    ):
        self.price_lookup = price_lookup
        self.ledger_path = Path(ledger_path)
        self.cash = initial_cash
        self._positions: dict[str, BrokerPosition] = {}
        self._load()

    # ------------------------------------------------------------ ledger io

    def _load(self) -> None:
        if not self.ledger_path.exists():
            return
        with open(self.ledger_path) as fh:
            data = json.load(fh)
        self.cash = float(data.get("cash", self.cash))
        for symbol, p in (data.get("positions") or {}).items():
            self._positions[symbol] = BrokerPosition(
                symbol=symbol,
                quantity=float(p["quantity"]),
                average_price=float(p["average_price"]),
            )

    def _save(self) -> None:
        payload = {
            "cash": self.cash,
            "positions": {
                s: {"quantity": p.quantity, "average_price": p.average_price}
                for s, p in self._positions.items()
            },
        }
        fd, tmp = tempfile.mkstemp(
            dir=str(self.ledger_path.parent) or ".", prefix=".paper_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.ledger_path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -------------------------------------------------------------- broker

    def _price(self, symbol: str) -> float:
        price = self.price_lookup(symbol)
        if price is None or price <= 0:
            raise BrokerError(f"paper broker has no price for {symbol}")
        return float(price)

    def account(self) -> AccountSnapshot:
        value = 0.0
        for symbol, pos in self._positions.items():
            price = self.price_lookup(symbol)
            pos.current_price = price
            value += pos.quantity * (price if price else pos.average_price)
        return AccountSnapshot(equity=self.cash + value, cash=self.cash)

    def positions(self) -> dict[str, BrokerPosition]:
        return {s: p for s, p in self._positions.items() if p.quantity > 0}

    def buy_market(self, symbol: str, quantity: float) -> dict:
        quantity = abs(quantity)
        price = self._price(symbol)
        cost = price * quantity
        if cost > self.cash:
            raise BrokerError(
                f"insufficient paper cash: need {cost:.2f}, have {self.cash:.2f}"
            )
        self.cash -= cost
        existing = self._positions.get(symbol)
        if existing:
            total_qty = existing.quantity + quantity
            existing.average_price = (
                existing.average_price * existing.quantity + price * quantity
            ) / total_qty
            existing.quantity = total_qty
        else:
            self._positions[symbol] = BrokerPosition(
                symbol=symbol, quantity=quantity, average_price=price
            )
        self._save()
        return {"paper": True, "symbol": symbol, "quantity": quantity, "fill": price}

    def sell_market(self, symbol: str, quantity: float) -> dict:
        quantity = abs(quantity)
        pos = self._positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            raise BrokerError(f"no paper position in {symbol}")
        quantity = min(quantity, pos.quantity)
        price = self._price(symbol)
        self.cash += price * quantity
        pos.quantity -= quantity
        if pos.quantity <= 1e-12:
            del self._positions[symbol]
        self._save()
        return {"paper": True, "symbol": symbol, "quantity": -quantity, "fill": price}
