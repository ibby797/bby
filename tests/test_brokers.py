"""Tests for the broker abstraction, mainly the built-in paper broker."""

import pytest

from quantbot.brokers import build_broker
from quantbot.brokers.base import BrokerError
from quantbot.brokers.paper import PaperBroker
from quantbot.config import Config


@pytest.fixture()
def prices():
    table = {"AAPL": 200.0, "MSFT": 400.0}
    return lambda s: table.get(s)


@pytest.fixture()
def broker(tmp_path, prices):
    return PaperBroker(prices, initial_cash=10_000.0, ledger_path=tmp_path / "ledger.json")


def test_paper_buy_sell_roundtrip(broker):
    broker.buy_market("AAPL", 10)
    snap = broker.account()
    assert snap.cash == pytest.approx(8_000.0)
    assert snap.equity == pytest.approx(10_000.0)  # cash + 10 x 200
    assert broker.positions()["AAPL"].quantity == 10

    broker.sell_market("AAPL", 10)
    snap = broker.account()
    assert snap.cash == pytest.approx(10_000.0)
    assert broker.positions() == {}


def test_paper_average_price_on_add(broker):
    broker.buy_market("AAPL", 10)          # @200
    broker.price_lookup = lambda s: 300.0  # price moves
    broker.buy_market("AAPL", 10)          # @300
    pos = broker.positions()["AAPL"]
    assert pos.quantity == 20
    assert pos.average_price == pytest.approx(250.0)


def test_paper_rejects_overspend_and_ghost_sells(broker):
    with pytest.raises(BrokerError, match="insufficient"):
        broker.buy_market("MSFT", 100)  # 40k > 10k cash
    with pytest.raises(BrokerError, match="no paper position"):
        broker.sell_market("MSFT", 1)


def test_paper_sell_clamps_to_held_quantity(broker):
    broker.buy_market("AAPL", 5)
    result = broker.sell_market("AAPL", 999)
    assert result["quantity"] == -5
    assert broker.positions() == {}


def test_paper_ledger_persists(tmp_path, prices):
    path = tmp_path / "ledger.json"
    b1 = PaperBroker(prices, initial_cash=10_000.0, ledger_path=path)
    b1.buy_market("AAPL", 3)
    b2 = PaperBroker(prices, initial_cash=999.0, ledger_path=path)  # reload
    assert b2.cash == pytest.approx(10_000.0 - 600.0)
    assert b2.positions()["AAPL"].quantity == 3


def test_factory_builds_paper(tmp_path, monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "x")
    cfg = Config.load(None)
    cfg.broker.kind = "paper"
    cfg.broker.paper_ledger_path = str(tmp_path / "l.json")
    broker = build_broker(cfg, price_lookup=lambda s: 100.0)
    assert broker.name == "paper"
    assert broker.real_money is False


def test_factory_rejects_unknown(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "x")
    cfg = Config.load(None)
    cfg.broker.kind = "totally-made-up"
    with pytest.raises(BrokerError, match="unknown broker"):
        build_broker(cfg)


def test_factory_paper_requires_prices(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "x")
    cfg = Config.load(None)
    cfg.broker.kind = "paper"
    with pytest.raises(BrokerError, match="price_lookup"):
        build_broker(cfg)
