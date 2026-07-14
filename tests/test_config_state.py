import pytest

from quantbot.config import Config, LIVE_CONFIRMATION_PHRASE
from quantbot.state import BotState, ManagedPosition, load_state, save_state


def test_defaults_are_safe(tmp_path, monkeypatch):
    monkeypatch.delenv("T212_API_KEY", raising=False)
    monkeypatch.delenv("T212_ENV", raising=False)
    monkeypatch.delenv("T212_DRY_RUN", raising=False)
    cfg = Config.load(tmp_path / "missing.yaml")
    assert cfg.environment == "demo"
    assert cfg.dry_run is True
    assert cfg.instruments  # default universe present


def test_live_without_confirmation_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("T212_ENV", raising=False)
    monkeypatch.delenv("T212_DRY_RUN", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("environment: live\ndry_run: false\n")
    with pytest.raises(ValueError, match="Refusing real-money trading"):
        Config.load(path)


def test_alpaca_live_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANTBOT_DRY_RUN", raising=False)
    monkeypatch.delenv("T212_DRY_RUN", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        "broker: alpaca\ndry_run: false\nbrokers:\n  alpaca:\n    paper: false\n"
    )
    with pytest.raises(ValueError, match="Refusing real-money trading"):
        Config.load(path)


def test_ccxt_always_real_money_unless_sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANTBOT_DRY_RUN", raising=False)
    monkeypatch.delenv("T212_DRY_RUN", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("broker: ccxt\ndry_run: false\n")
    with pytest.raises(ValueError, match="Refusing real-money trading"):
        Config.load(path)
    path.write_text(
        "broker: ccxt\ndry_run: false\nbrokers:\n  ccxt:\n    sandbox: true\n"
    )
    cfg = Config.load(path)  # sandbox = no confirmation needed
    assert cfg.is_real_money() is False


def test_paper_broker_never_needs_confirmation(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANTBOT_DRY_RUN", raising=False)
    monkeypatch.delenv("T212_DRY_RUN", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("broker: paper\ndry_run: false\n")
    cfg = Config.load(path)
    assert cfg.broker.kind == "paper" and cfg.is_real_money() is False


def test_broker_and_regime_parsing(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANTBOT_BROKER", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        """
broker: ccxt
brokers:
  ccxt:
    exchange: kraken
    quote: usd
    sandbox: true
  paper:
    initial_cash: 5000
regime:
  enabled: true
  symbol: QQQ
  sma_window: 100
"""
    )
    cfg = Config.load(path)
    assert cfg.broker.kind == "ccxt"
    assert cfg.broker.ccxt_exchange == "kraken"
    assert cfg.broker.ccxt_quote == "USD"
    assert cfg.broker.paper_initial_cash == 5000.0
    assert cfg.regime.enabled and cfg.regime.symbol == "QQQ"
    assert cfg.regime.sma_window == 100


def test_live_with_confirmation_accepted(tmp_path, monkeypatch):
    monkeypatch.delenv("T212_ENV", raising=False)
    monkeypatch.delenv("T212_DRY_RUN", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        f"environment: live\ndry_run: false\nconfirm_live: {LIVE_CONFIRMATION_PHRASE}\n"
    )
    cfg = Config.load(path)
    assert cfg.environment == "live" and cfg.dry_run is False


def test_yaml_and_env_overrides(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
environment: demo
risk:
  risk_per_trade_pct: 2.0
  max_open_positions: 3
strategy:
  min_entry_score: 0.5
  weights:
    sma_crossover: 2.0
universe:
  instruments:
    AAPL_US_EQ: AAPL
"""
    )
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_DRY_RUN", "false")
    cfg = Config.load(path)
    assert cfg.api_key == "test-key"
    assert cfg.dry_run is False
    assert cfg.risk.risk_per_trade_pct == 2.0
    assert cfg.risk.max_open_positions == 3
    assert cfg.strategy.min_entry_score == 0.5
    assert cfg.strategy.weights["sma_crossover"] == 2.0
    assert cfg.instruments == {"AAPL_US_EQ": "AAPL"}


def test_state_roundtrip(tmp_path):
    state = BotState(peak_equity=12_345.0, day_date="2026-07-13", day_start_equity=12_000.0)
    state.positions["AAPL_US_EQ"] = ManagedPosition(
        ticker="AAPL_US_EQ",
        yahoo_symbol="AAPL",
        quantity=1.5,
        entry_price=200.0,
        entry_time="2026-07-13T14:30:00+00:00",
        stop_price=190.0,
        take_profit_price=220.0,
        highest_close=205.0,
        atr_at_entry=4.0,
    )
    path = tmp_path / "state.json"
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.peak_equity == 12_345.0
    assert loaded.positions["AAPL_US_EQ"].stop_price == 190.0
    assert loaded.positions["AAPL_US_EQ"].quantity == 1.5


def test_load_missing_state_returns_fresh(tmp_path):
    state = load_state(tmp_path / "nope.json")
    assert state.positions == {} and state.peak_equity == 0.0
