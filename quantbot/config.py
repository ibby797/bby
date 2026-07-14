"""Configuration: YAML file + environment variable overrides.

Safety model:
  * The broker defaults to Trading 212 **demo**, and ``dry_run`` defaults to
    ``true`` (orders are logged, never sent).
  * Any real-money configuration (Trading 212 live, Alpaca live, CCXT without
    sandbox) with ``dry_run: false`` requires
    ``confirm_live: I_UNDERSTAND_REAL_MONEY_IS_AT_RISK`` in the config file.
    There is deliberately no env-var shortcut for the confirmation.

Secrets come from environment variables, never the config file:
  * Trading 212: ``T212_API_KEY``
  * Alpaca:      ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``
  * CCXT:        ``CCXT_API_KEY`` / ``CCXT_SECRET``
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .risk.manager import RiskConfig

LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_REAL_MONEY_IS_AT_RISK"

DEFAULT_UNIVERSE = {
    # Broker symbol -> Yahoo Finance data symbol (Trading 212 notation shown)
    "AAPL_US_EQ": "AAPL",
    "MSFT_US_EQ": "MSFT",
    "GOOGL_US_EQ": "GOOGL",
    "AMZN_US_EQ": "AMZN",
    "NVDA_US_EQ": "NVDA",
    "META_US_EQ": "META",
    "TSLA_US_EQ": "TSLA",
    "JPM_US_EQ": "JPM",
    "V_US_EQ": "V",
    "JNJ_US_EQ": "JNJ",
}

BROKER_KINDS = ("trading212", "alpaca", "ccxt", "paper")


@dataclass
class BrokerConfig:
    kind: str = "trading212"
    # alpaca
    alpaca_paper: bool = True
    # ccxt
    ccxt_exchange: str = "binance"
    ccxt_quote: str = "USDT"
    ccxt_sandbox: bool = False
    # built-in paper broker
    paper_initial_cash: float = 10_000.0
    paper_ledger_path: str = "paper_ledger.json"


@dataclass
class StrategyConfig:
    min_entry_score: float = 0.25   # ensemble score needed to open
    exit_score: float = -0.25       # ensemble score that forces an exit
    weights: dict = field(default_factory=dict)  # per-strategy weight overrides


@dataclass
class RegimeConfig:
    """Market-regime filter: block NEW entries while a benchmark trades below
    its long moving average (exits keep working). One of the simplest and most
    effective drawdown reducers in systematic trading."""

    enabled: bool = False
    symbol: str = "SPY"     # Yahoo symbol of the benchmark
    sma_window: int = 200


@dataclass
class DataConfig:
    interval: str = "1d"
    lookback_days: int = 400
    cache_ttl_seconds: float = 300.0


@dataclass
class ScheduleConfig:
    poll_seconds: int = 300
    market_hours_only: bool = True  # set false for 24/7 markets (crypto)


@dataclass
class Config:
    environment: str = "demo"       # Trading 212 environment: demo | live
    dry_run: bool = True
    confirm_live: str = ""
    api_key: str = ""
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    instruments: dict = field(default_factory=lambda: dict(DEFAULT_UNIVERSE))
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    data: DataConfig = field(default_factory=DataConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    state_path: str = "state.json"
    webhook_url: str = ""  # optional Slack/Discord-compatible notification hook

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        raw: dict = {}
        if path is not None and Path(path).exists():
            with open(path) as fh:
                raw = yaml.safe_load(fh) or {}

        cfg = cls()
        cfg.environment = str(raw.get("environment", cfg.environment)).lower()
        cfg.dry_run = bool(raw.get("dry_run", cfg.dry_run))
        cfg.confirm_live = str(raw.get("confirm_live", ""))
        cfg.state_path = str(raw.get("state_path", cfg.state_path))
        cfg.webhook_url = str(raw.get("webhook_url", ""))

        api = raw.get("api") or {}
        cfg.api_key = os.environ.get("T212_API_KEY") or str(api.get("key") or "")

        brokers = raw.get("brokers") or {}
        alpaca = brokers.get("alpaca") or {}
        ccxt_cfg = brokers.get("ccxt") or {}
        paper = brokers.get("paper") or {}
        cfg.broker = BrokerConfig(
            kind=str(raw.get("broker", "trading212")).lower(),
            alpaca_paper=bool(alpaca.get("paper", True)),
            ccxt_exchange=str(ccxt_cfg.get("exchange", "binance")).lower(),
            ccxt_quote=str(ccxt_cfg.get("quote", "USDT")).upper(),
            ccxt_sandbox=bool(ccxt_cfg.get("sandbox", False)),
            paper_initial_cash=float(paper.get("initial_cash", 10_000.0)),
            paper_ledger_path=str(paper.get("ledger_path", "paper_ledger.json")),
        )

        universe = raw.get("universe") or {}
        if universe.get("instruments"):
            cfg.instruments = {
                str(k): str(v) for k, v in universe["instruments"].items()
            }

        strat = raw.get("strategy") or {}
        cfg.strategy = StrategyConfig(
            min_entry_score=float(strat.get("min_entry_score", 0.25)),
            exit_score=float(strat.get("exit_score", -0.25)),
            weights={str(k): float(v) for k, v in (strat.get("weights") or {}).items()},
        )

        regime = raw.get("regime") or {}
        cfg.regime = RegimeConfig(
            enabled=bool(regime.get("enabled", False)),
            symbol=str(regime.get("symbol", "SPY")),
            sma_window=int(regime.get("sma_window", 200)),
        )

        data = raw.get("data") or {}
        cfg.data = DataConfig(
            interval=str(data.get("interval", "1d")),
            lookback_days=int(data.get("lookback_days", 400)),
            cache_ttl_seconds=float(data.get("cache_ttl_seconds", 300.0)),
        )

        risk = raw.get("risk") or {}
        cfg.risk = RiskConfig(
            risk_per_trade_pct=float(risk.get("risk_per_trade_pct", 1.0)),
            max_position_pct=float(risk.get("max_position_pct", 20.0)),
            max_total_exposure_pct=float(risk.get("max_total_exposure_pct", 90.0)),
            max_open_positions=int(risk.get("max_open_positions", 5)),
            min_cash_buffer_pct=float(risk.get("min_cash_buffer_pct", 5.0)),
            atr_stop_multiplier=float(risk.get("atr_stop_multiplier", 2.5)),
            atr_take_profit_multiplier=float(risk.get("atr_take_profit_multiplier", 5.0)),
            trailing_stop=bool(risk.get("trailing_stop", True)),
            max_daily_loss_pct=float(risk.get("max_daily_loss_pct", 3.0)),
            max_drawdown_pct=float(risk.get("max_drawdown_pct", 15.0)),
            quantity_decimals=int(risk.get("quantity_decimals", 4)),
            reentry_cooldown_hours=float(risk.get("reentry_cooldown_hours", 24.0)),
        )

        sched = raw.get("schedule") or {}
        cfg.schedule = ScheduleConfig(
            poll_seconds=int(sched.get("poll_seconds", 300)),
            market_hours_only=bool(sched.get("market_hours_only", True)),
        )

        # Env overrides for non-secret knobs (QUANTBOT_* preferred, T212_*
        # accepted for backward compatibility).
        env_broker = os.environ.get("QUANTBOT_BROKER")
        if env_broker:
            cfg.broker.kind = env_broker.lower()
        env_environment = os.environ.get("QUANTBOT_ENV") or os.environ.get("T212_ENV")
        if env_environment:
            cfg.environment = env_environment.lower()
        env_dry = os.environ.get("QUANTBOT_DRY_RUN") or os.environ.get("T212_DRY_RUN")
        if env_dry is not None:
            cfg.dry_run = env_dry.strip().lower() not in ("0", "false", "no")

        cfg.validate()
        return cfg

    def is_real_money(self) -> bool:
        """True when the configured broker would move real funds."""
        kind = self.broker.kind
        if kind == "paper":
            return False
        if kind == "trading212":
            return self.environment == "live"
        if kind == "alpaca":
            return not self.broker.alpaca_paper
        if kind == "ccxt":
            return not self.broker.ccxt_sandbox
        return True  # unknown broker: assume the dangerous case

    def validate(self) -> None:
        if self.environment not in ("demo", "live"):
            raise ValueError("environment must be 'demo' or 'live'")
        if self.broker.kind not in BROKER_KINDS:
            raise ValueError(f"broker must be one of {BROKER_KINDS}")
        if self.is_real_money() and not self.dry_run:
            if self.confirm_live != LIVE_CONFIRMATION_PHRASE:
                raise ValueError(
                    "Refusing real-money trading: set confirm_live: "
                    f"{LIVE_CONFIRMATION_PHRASE} in the config file to acknowledge "
                    "that real money is at risk and losses are possible."
                )
        if not self.instruments:
            raise ValueError("universe.instruments must not be empty")
        self.risk.validate()
