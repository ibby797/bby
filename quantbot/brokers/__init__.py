"""Broker adapters + factory.

Supported venues:
  * ``trading212`` — Trading 212 Invest/ISA (demo & live)
  * ``alpaca``     — Alpaca US stocks/ETFs (paper & live)
  * ``ccxt``       — 100+ crypto exchanges via ccxt (optional dependency)
  * ``paper``      — built-in local simulator, no account needed anywhere
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import AccountSnapshot, Broker, BrokerError, BrokerPosition
from .paper import PaperBroker, PriceLookup

if TYPE_CHECKING:
    from ..config import Config

__all__ = [
    "AccountSnapshot",
    "Broker",
    "BrokerError",
    "BrokerPosition",
    "PaperBroker",
    "build_broker",
]


def build_broker(config: "Config", price_lookup: PriceLookup | None = None) -> Broker:
    kind = config.broker.kind
    if kind == "trading212":
        from .trading212 import Trading212Broker

        return Trading212Broker(config.api_key, config.environment)
    if kind == "paper":
        if price_lookup is None:
            raise BrokerError("paper broker needs a price_lookup (market data source)")
        return PaperBroker(
            price_lookup=price_lookup,
            initial_cash=config.broker.paper_initial_cash,
            ledger_path=config.broker.paper_ledger_path,
        )
    if kind == "alpaca":
        from .alpaca import AlpacaBroker

        return AlpacaBroker(paper=config.broker.alpaca_paper)
    if kind == "ccxt":
        from .ccxt_broker import CcxtBroker

        return CcxtBroker(
            exchange_id=config.broker.ccxt_exchange,
            quote_currency=config.broker.ccxt_quote,
            sandbox=config.broker.ccxt_sandbox,
        )
    raise BrokerError(f"unknown broker kind: {kind!r} "
                      "(expected trading212, alpaca, ccxt, or paper)")
