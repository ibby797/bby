"""Broker abstraction.

Every venue quantbot can trade through implements this small interface. The
bot core (signals, risk, state) never talks to a venue directly, so adding a
new trading app means writing one adapter class — nothing else changes.

Symbol convention: the universe config maps *broker symbol* -> *Yahoo data
symbol* (e.g. ``AAPL_US_EQ: AAPL`` for Trading 212, ``AAPL: AAPL`` for Alpaca,
``BTC/USDT: BTC-USD`` for a CCXT crypto exchange). All Broker methods take the
broker symbol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class BrokerError(Exception):
    """Raised by adapters for venue-side failures (auth, rejects, network)."""


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float        # total account value
    cash: float          # free/spendable cash
    currency: str = ""


@dataclass
class BrokerPosition:
    symbol: str
    quantity: float
    average_price: float
    current_price: float | None = None


class Broker(ABC):
    name: str = "broker"
    real_money: bool = False  # adapters set True when orders move real funds

    @abstractmethod
    def account(self) -> AccountSnapshot:
        """Current equity and free cash."""

    @abstractmethod
    def positions(self) -> dict[str, BrokerPosition]:
        """Open positions keyed by broker symbol."""

    @abstractmethod
    def buy_market(self, symbol: str, quantity: float) -> dict:
        """Market-buy ``quantity`` of ``symbol``; returns the venue response."""

    @abstractmethod
    def sell_market(self, symbol: str, quantity: float) -> dict:
        """Market-sell ``quantity`` (positive number) of ``symbol``."""
