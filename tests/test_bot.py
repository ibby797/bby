"""End-to-end bot cycle tests against a fake broker and fake market data."""

import pandas as pd
import pytest

from t212bot.bot import TradingBot, us_market_open_now
from t212bot.config import Config
from t212bot.state import ManagedPosition
from tests.helpers import trending_up, trending_down


class FakeClient:
    environment = "demo"

    def __init__(self, cash=None, portfolio=None):
        self.cash = cash or {"free": 10_000.0, "invested": 0.0, "total": 10_000.0}
        self.portfolio = portfolio or []
        self.orders = []

    def get_account_cash(self):
        return self.cash

    def get_portfolio(self):
        return self.portfolio

    def place_market_order(self, ticker, quantity):
        self.orders.append({"ticker": ticker, "quantity": quantity})
        return {"id": len(self.orders), "ticker": ticker, "quantity": quantity}


class FakeData:
    def __init__(self, frames):
        self.frames = frames

    def history(self, symbol):
        return self.frames.get(symbol, pd.DataFrame())


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.delenv("T212_ENV", raising=False)
    monkeypatch.delenv("T212_DRY_RUN", raising=False)
    config = Config.load(None)
    config.state_path = str(tmp_path / "state.json")
    config.instruments = {"UP_US_EQ": "UP", "DOWN_US_EQ": "DOWN"}
    return config


def make_bot(cfg, client, frames, dry_run=False):
    cfg.dry_run = dry_run
    bot = TradingBot(cfg)
    bot.client = client
    bot.executor.client = client
    bot.executor.dry_run = dry_run
    bot.data = FakeData(frames)
    return bot


def test_cycle_buys_uptrend_not_downtrend(cfg):
    client = FakeClient()
    bot = make_bot(cfg, client, {"UP": trending_up(300), "DOWN": trending_down(300)})
    bot.run_once()
    tickers_bought = {o["ticker"] for o in client.orders}
    assert "UP_US_EQ" in tickers_bought
    assert "DOWN_US_EQ" not in tickers_bought
    assert "UP_US_EQ" in bot.state.positions
    pos = bot.state.positions["UP_US_EQ"]
    assert pos.stop_price < pos.entry_price < pos.take_profit_price


def test_dry_run_never_calls_broker(cfg):
    client = FakeClient()
    bot = make_bot(cfg, client, {"UP": trending_up(300)}, dry_run=True)
    bot.run_once()
    assert client.orders == []  # decisions logged, nothing sent
    assert "UP_US_EQ" in bot.state.positions  # but tracked for inspection


def test_stop_loss_triggers_market_sell(cfg):
    up = trending_up(300)
    price_now = float(up["Close"].iloc[-1])
    client = FakeClient(
        cash={"free": 5_000.0, "invested": 5_000.0, "total": 10_000.0},
        portfolio=[{"ticker": "UP_US_EQ", "quantity": 10.0, "averagePrice": price_now}],
    )
    bot = make_bot(cfg, client, {"UP": up})
    bot.state.positions["UP_US_EQ"] = ManagedPosition(
        ticker="UP_US_EQ",
        yahoo_symbol="UP",
        quantity=10.0,
        entry_price=price_now * 1.2,
        entry_time="2026-07-01T14:30:00+00:00",
        stop_price=price_now * 1.1,  # already breached
        take_profit_price=price_now * 2.0,
        highest_close=price_now * 1.2,
        atr_at_entry=price_now * 0.02,
    )
    bot.run_once()
    sells = [o for o in client.orders if o["ticker"] == "UP_US_EQ" and o["quantity"] < 0]
    assert len(sells) == 1 and sells[0]["quantity"] == -10.0
    assert "UP_US_EQ" not in bot.state.positions


def test_daily_loss_halts_new_entries(cfg):
    client = FakeClient(cash={"free": 9_600.0, "invested": 0.0, "total": 9_600.0})
    bot = make_bot(cfg, client, {"UP": trending_up(300)})
    bot.state.day_date = pd.Timestamp.today().date().isoformat()
    bot.state.day_start_equity = 10_000.0  # -4% today >= 3% limit
    bot.state.peak_equity = 10_000.0
    bot.run_once()
    assert client.orders == []
    assert bot.state.halted


def test_reconcile_drops_externally_closed_position(cfg):
    client = FakeClient()  # empty portfolio
    bot = make_bot(cfg, client, {"UP": trending_up(300)}, dry_run=True)
    bot.state.positions["GONE_US_EQ"] = ManagedPosition(
        ticker="GONE_US_EQ", yahoo_symbol="GONE", quantity=1.0, entry_price=10.0,
        entry_time="t", stop_price=9.0, take_profit_price=12.0,
        highest_close=10.0, atr_at_entry=0.5,
    )
    bot.run_once()
    assert "GONE_US_EQ" not in bot.state.positions


def test_market_hours_helper():
    import datetime as dt

    tz = dt.timezone.utc
    assert us_market_open_now(dt.datetime(2026, 7, 13, 15, 0, tzinfo=tz))   # Mon 15:00
    assert not us_market_open_now(dt.datetime(2026, 7, 13, 13, 0, tzinfo=tz))  # pre-open
    assert not us_market_open_now(dt.datetime(2026, 7, 12, 15, 0, tzinfo=tz))  # Sunday
