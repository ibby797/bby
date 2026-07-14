"""End-to-end bot cycle tests against a fake broker and fake market data."""

import pandas as pd
import pytest

from quantbot.bot import TradingBot, us_market_open_now
from quantbot.brokers.base import AccountSnapshot, Broker, BrokerPosition
from quantbot.config import Config
from quantbot.state import ManagedPosition
from tests.helpers import trending_up, trending_down


class FakeBroker(Broker):
    name = "fake"
    real_money = False

    def __init__(self, equity=10_000.0, cash=10_000.0, portfolio=None):
        self.equity = equity
        self.cash = cash
        self.portfolio = portfolio or {}
        self.orders = []

    def account(self):
        return AccountSnapshot(equity=self.equity, cash=self.cash)

    def positions(self):
        return dict(self.portfolio)

    def buy_market(self, symbol, quantity):
        self.orders.append({"symbol": symbol, "quantity": quantity})
        return {"symbol": symbol, "quantity": quantity}

    def sell_market(self, symbol, quantity):
        self.orders.append({"symbol": symbol, "quantity": -abs(quantity)})
        return {"symbol": symbol, "quantity": -abs(quantity)}


class FakeData:
    def __init__(self, frames):
        self.frames = frames

    def history(self, symbol):
        return self.frames.get(symbol, pd.DataFrame())

    def latest_price(self, symbol):
        df = self.history(symbol)
        return float(df["Close"].iloc[-1]) if len(df) else None


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    for var in ("T212_ENV", "T212_DRY_RUN", "QUANTBOT_ENV", "QUANTBOT_DRY_RUN",
                "QUANTBOT_BROKER"):
        monkeypatch.delenv(var, raising=False)
    config = Config.load(None)
    config.state_path = str(tmp_path / "state.json")
    config.instruments = {"UP_US_EQ": "UP", "DOWN_US_EQ": "DOWN"}
    return config


def make_bot(cfg, broker, frames, dry_run=False):
    cfg.dry_run = dry_run
    bot = TradingBot(cfg)
    bot.broker = broker
    bot.executor.broker = broker
    bot.executor.dry_run = dry_run
    bot.data = FakeData(frames)
    return bot


def test_cycle_buys_uptrend_not_downtrend(cfg):
    broker = FakeBroker()
    bot = make_bot(cfg, broker, {"UP": trending_up(300), "DOWN": trending_down(300)})
    bot.run_once()
    symbols_bought = {o["symbol"] for o in broker.orders}
    assert "UP_US_EQ" in symbols_bought
    assert "DOWN_US_EQ" not in symbols_bought
    assert "UP_US_EQ" in bot.state.positions
    pos = bot.state.positions["UP_US_EQ"]
    assert pos.stop_price < pos.entry_price < pos.take_profit_price


def test_dry_run_never_calls_broker(cfg):
    broker = FakeBroker()
    bot = make_bot(cfg, broker, {"UP": trending_up(300)}, dry_run=True)
    bot.run_once()
    assert broker.orders == []  # decisions logged, nothing sent
    assert "UP_US_EQ" in bot.state.positions  # but tracked for inspection


def test_stop_loss_triggers_market_sell(cfg):
    up = trending_up(300)
    price_now = float(up["Close"].iloc[-1])
    broker = FakeBroker(
        equity=10_000.0,
        cash=5_000.0,
        portfolio={
            "UP_US_EQ": BrokerPosition(
                symbol="UP_US_EQ", quantity=10.0, average_price=price_now
            )
        },
    )
    bot = make_bot(cfg, broker, {"UP": up})
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
    sells = [o for o in broker.orders if o["symbol"] == "UP_US_EQ" and o["quantity"] < 0]
    assert len(sells) == 1 and sells[0]["quantity"] == -10.0
    assert "UP_US_EQ" not in bot.state.positions
    # whipsaw guard: the exit started a re-entry cooldown
    assert "UP_US_EQ" in bot.state.cooldowns


def test_daily_loss_halts_new_entries(cfg):
    broker = FakeBroker(equity=9_600.0, cash=9_600.0)
    bot = make_bot(cfg, broker, {"UP": trending_up(300)})
    bot.state.day_date = pd.Timestamp.today().date().isoformat()
    bot.state.day_start_equity = 10_000.0  # -4% today >= 3% limit
    bot.state.peak_equity = 10_000.0
    bot.run_once()
    assert broker.orders == []
    assert bot.state.halted


def test_reconcile_drops_externally_closed_position(cfg):
    broker = FakeBroker()  # empty portfolio
    bot = make_bot(cfg, broker, {"UP": trending_up(300)}, dry_run=True)
    bot.state.positions["GONE_US_EQ"] = ManagedPosition(
        ticker="GONE_US_EQ", yahoo_symbol="GONE", quantity=1.0, entry_price=10.0,
        entry_time="t", stop_price=9.0, take_profit_price=12.0,
        highest_close=10.0, atr_at_entry=0.5,
    )
    bot.run_once()
    assert "GONE_US_EQ" not in bot.state.positions


def test_regime_filter_blocks_entries(cfg):
    cfg.regime.enabled = True
    cfg.regime.symbol = "BENCH"
    cfg.regime.sma_window = 50
    broker = FakeBroker()
    # strong universe uptrend, but the benchmark is in a downtrend => risk-off
    bot = make_bot(cfg, broker, {"UP": trending_up(300), "BENCH": trending_down(300)})
    bot.run_once()
    assert broker.orders == []
    assert bot.state.positions == {}


def test_regime_filter_allows_when_benchmark_strong(cfg):
    cfg.regime.enabled = True
    cfg.regime.symbol = "BENCH"
    cfg.regime.sma_window = 50
    broker = FakeBroker()
    bot = make_bot(cfg, broker, {"UP": trending_up(300), "BENCH": trending_up(300)})
    bot.run_once()
    assert any(o["quantity"] > 0 for o in broker.orders)


def test_regime_filter_fails_open_without_data(cfg):
    cfg.regime.enabled = True
    cfg.regime.symbol = "MISSING"
    broker = FakeBroker()
    bot = make_bot(cfg, broker, {"UP": trending_up(300)})
    allowed, reason = bot.regime_allows_entries()
    assert allowed and "fail-open" in reason


def test_short_entry_on_downtrend(cfg):
    cfg.strategy.allow_short = True
    broker = FakeBroker()
    broker.supports_short = True
    bot = make_bot(cfg, broker, {"UP": trending_up(300), "DOWN": trending_down(300)})
    bot.run_once()
    shorts = [o for o in broker.orders if o["symbol"] == "DOWN_US_EQ" and o["quantity"] < 0]
    assert len(shorts) == 1
    pos = bot.state.positions["DOWN_US_EQ"]
    assert pos.direction == -1
    # short stops mirror: stop above entry, take-profit below
    assert pos.stop_price > pos.entry_price > pos.take_profit_price


def test_no_short_on_unsupporting_broker(cfg):
    cfg.strategy.allow_short = True
    broker = FakeBroker()  # supports_short = False (like Trading 212)
    bot = make_bot(cfg, broker, {"DOWN": trending_down(300)})
    bot.run_once()
    assert broker.orders == []  # wanted to short, broker can't, so nothing
    assert bot.state.positions == {}


def test_short_stop_triggers_cover(cfg):
    cfg.strategy.allow_short = True
    up = trending_up(300)  # price has rallied against the short
    price_now = float(up["Close"].iloc[-1])
    broker = FakeBroker(
        equity=10_000.0,
        cash=11_000.0,
        portfolio={
            "UP_US_EQ": BrokerPosition(
                symbol="UP_US_EQ", quantity=-10.0, average_price=price_now * 0.8
            )
        },
    )
    broker.supports_short = True
    bot = make_bot(cfg, broker, {"UP": up})
    bot.state.positions["UP_US_EQ"] = ManagedPosition(
        ticker="UP_US_EQ",
        yahoo_symbol="UP",
        quantity=10.0,
        entry_price=price_now * 0.8,
        entry_time="2026-07-01T14:30:00+00:00",
        stop_price=price_now * 0.9,  # already breached (price above stop)
        take_profit_price=price_now * 0.5,
        highest_close=price_now * 0.8,
        atr_at_entry=price_now * 0.02,
        direction=-1,
    )
    bot.run_once()
    covers = [o for o in broker.orders if o["symbol"] == "UP_US_EQ" and o["quantity"] > 0]
    assert len(covers) == 1 and covers[0]["quantity"] == 10.0
    assert "UP_US_EQ" not in bot.state.positions


def test_adopts_external_short_position(cfg):
    cfg.strategy.allow_short = True
    down = trending_down(300)
    price_now = float(down["Close"].iloc[-1])
    broker = FakeBroker(
        equity=10_000.0,
        cash=11_000.0,
        portfolio={
            "DOWN_US_EQ": BrokerPosition(
                # entered just above current price: no stop/take-profit fires,
                # so the adopted position must survive the exit pass
                symbol="DOWN_US_EQ", quantity=-5.0, average_price=price_now * 1.02
            )
        },
    )
    broker.supports_short = True
    bot = make_bot(cfg, broker, {"DOWN": down}, dry_run=True)
    bot.run_once()
    pos = bot.state.positions.get("DOWN_US_EQ")
    assert pos is not None and pos.direction == -1 and pos.quantity == 5.0
    assert pos.stop_price > pos.entry_price  # short stop sits above


def test_market_hours_helper():
    import datetime as dt

    tz = dt.timezone.utc
    assert us_market_open_now(dt.datetime(2026, 7, 13, 15, 0, tzinfo=tz))   # Mon 15:00
    assert not us_market_open_now(dt.datetime(2026, 7, 13, 13, 0, tzinfo=tz))  # pre-open
    assert not us_market_open_now(dt.datetime(2026, 7, 12, 15, 0, tzinfo=tz))  # Sunday
