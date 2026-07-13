#!/usr/bin/env python3
"""t212bot command-line interface.

Commands:
  account       Show account info, cash, and open positions.
  instruments   Search the tradable instrument list (find exact T212 tickers).
  backtest      Backtest the ensemble over the configured universe.
  once          Run a single live trading cycle.
  run           Run the trading loop until interrupted.

Every command takes --config (default: config.yaml, falling back to built-in
defaults). The API key comes from the T212_API_KEY environment variable.
"""

from __future__ import annotations

import argparse
import logging
import sys

from t212bot.config import Config


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_account(cfg: Config, _args) -> int:
    from t212bot.api.client import Trading212Client

    client = Trading212Client(cfg.api_key, cfg.environment)
    info = client.get_account_info()
    cash = client.get_account_cash()
    portfolio = client.get_portfolio()
    print(f"Environment : {cfg.environment}")
    print(f"Account     : id={info.get('id')} currency={info.get('currencyCode')}")
    print(f"Cash        : free={cash.get('free')} invested={cash.get('invested')} "
          f"result={cash.get('result')} total={cash.get('total')}")
    print(f"Positions   : {len(portfolio)}")
    for p in portfolio:
        print(f"  {p.get('ticker'):<16} qty={p.get('quantity')} "
              f"avg={p.get('averagePrice')} now={p.get('currentPrice')} ppl={p.get('ppl')}")
    return 0


def cmd_instruments(cfg: Config, args) -> int:
    from t212bot.api.client import Trading212Client

    client = Trading212Client(cfg.api_key, cfg.environment)
    query = (args.search or "").lower()
    matches = [
        inst for inst in client.get_instruments()
        if query in str(inst.get("ticker", "")).lower()
        or query in str(inst.get("name", "")).lower()
        or query in str(inst.get("shortName", "")).lower()
    ]
    for inst in matches[: args.limit]:
        print(f"{inst.get('ticker'):<24} {inst.get('type'):<10} "
              f"{inst.get('currencyCode'):<5} min={inst.get('minTradeQuantity')} "
              f"{inst.get('name')}")
    print(f"\n{len(matches)} match(es)")
    return 0


def cmd_backtest(cfg: Config, args) -> int:
    from t212bot.backtest.engine import BacktestEngine, BacktestSettings
    from t212bot.risk.manager import RiskConfig
    from t212bot.strategies import build_default_ensemble

    if args.csv_dir:
        from t212bot.data.market_data import load_csv_dir

        data = load_csv_dir(args.csv_dir)
    else:
        from t212bot.data.market_data import MarketDataProvider

        provider = MarketDataProvider(
            interval=cfg.data.interval, lookback_days=args.days
        )
        data = provider.history_map(list(cfg.instruments.values()))

    if not data:
        print("No data available for the configured universe.", file=sys.stderr)
        return 1
    print(f"Backtesting {len(data)} instruments: {', '.join(sorted(data))}\n")

    engine = BacktestEngine(
        strategy=build_default_ensemble(cfg.strategy.weights),
        risk_config=cfg.risk if isinstance(cfg.risk, RiskConfig) else RiskConfig(),
        settings=BacktestSettings(
            initial_cash=args.cash,
            min_entry_score=cfg.strategy.min_entry_score,
            exit_score=cfg.strategy.exit_score,
        ),
    )
    result = engine.run(data)
    print(result.summary())
    if args.trades:
        print("\nTrades:")
        for t in result.trades:
            print(f"  {t.symbol:<8} {t.entry_date.date()} -> {t.exit_date.date()} "
                  f"qty={t.quantity:g} {t.entry_price:.2f} -> {t.exit_price:.2f} "
                  f"pnl={t.pnl:+.2f} ({t.exit_reason})")
    return 0


def cmd_once(cfg: Config, _args) -> int:
    from t212bot.bot import TradingBot

    TradingBot(cfg).run_once()
    return 0


def cmd_run(cfg: Config, _args) -> int:
    from t212bot.bot import TradingBot

    mode = "DRY-RUN" if cfg.dry_run else cfg.environment.upper()
    print(f"Starting t212bot [{mode}] — Ctrl+C to stop.")
    if not cfg.dry_run and cfg.environment == "live":
        print("*** LIVE TRADING WITH REAL MONEY. Losses are possible. ***")
    TradingBot(cfg).run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="t212bot", description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="path to config YAML")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("account", help="show account info and positions")

    p_inst = sub.add_parser("instruments", help="search tradable instruments")
    p_inst.add_argument("--search", default="", help="substring to match")
    p_inst.add_argument("--limit", type=int, default=25)

    p_bt = sub.add_parser("backtest", help="backtest the ensemble")
    p_bt.add_argument("--days", type=int, default=730, help="history length to fetch")
    p_bt.add_argument("--cash", type=float, default=10_000.0, help="initial cash")
    p_bt.add_argument("--csv-dir", default="", help="load OHLCV CSVs instead of Yahoo")
    p_bt.add_argument("--trades", action="store_true", help="print every trade")

    sub.add_parser("once", help="run one live trading cycle")
    sub.add_parser("run", help="run the trading loop")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    cfg = Config.load(args.config)

    handlers = {
        "account": cmd_account,
        "instruments": cmd_instruments,
        "backtest": cmd_backtest,
        "once": cmd_once,
        "run": cmd_run,
    }
    return handlers[args.command](cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
