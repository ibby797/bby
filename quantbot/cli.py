"""quantbot command-line interface.

Commands:
  account       Show equity, cash, and open positions on the configured broker.
  instruments   Search Trading 212's tradable instrument list (T212 only).
  signals       Compute current ensemble signals; print and/or export to
                JSON/CSV so ANY trading app (or you) can act on them.
  backtest      Backtest the ensemble; optional self-contained HTML report.
  once          Run a single trading cycle.
  run           Run the trading loop until interrupted.

Every command takes --config (default: config.yaml, falling back to built-in
defaults). Secrets come from environment variables (T212_API_KEY,
APCA_API_KEY_ID/APCA_API_SECRET_KEY, CCXT_API_KEY/CCXT_SECRET).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import sys

from .config import Config

log = logging.getLogger(__name__)


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _build_broker(cfg: Config):
    from .brokers import build_broker
    from .data.market_data import MarketDataProvider

    provider = MarketDataProvider(
        interval=cfg.data.interval, lookback_days=cfg.data.lookback_days
    )
    return build_broker(
        cfg, price_lookup=lambda s: provider.latest_price(cfg.instruments.get(s, s))
    )


def cmd_account(cfg: Config, _args) -> int:
    broker = _build_broker(cfg)
    snap = broker.account()
    positions = broker.positions()
    print(f"Broker      : {broker.name} "
          f"({'REAL MONEY' if broker.real_money else 'paper/demo'})")
    print(f"Equity      : {snap.equity:,.2f} {snap.currency}")
    print(f"Free cash   : {snap.cash:,.2f} {snap.currency}")
    print(f"Positions   : {len(positions)}")
    for p in positions.values():
        now = f"{p.current_price:.2f}" if p.current_price else "?"
        print(f"  {p.symbol:<18} qty={p.quantity:g} avg={p.average_price:.2f} now={now}")
    return 0


def cmd_instruments(cfg: Config, args) -> int:
    if cfg.broker.kind != "trading212":
        print("instruments search is Trading 212-specific "
              f"(configured broker: {cfg.broker.kind})", file=sys.stderr)
        return 1
    from .api.client import Trading212Client

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


def cmd_signals(cfg: Config, args) -> int:
    """Score the whole universe now and export machine-readable signals."""
    from .data.market_data import MarketDataProvider
    from .indicators import atr as atr_indicator
    from .risk.manager import RiskManager
    from .strategies import build_default_ensemble

    provider = MarketDataProvider(
        interval=cfg.data.interval, lookback_days=cfg.data.lookback_days
    )
    strategy = build_default_ensemble(cfg.strategy.weights)
    risk = RiskManager(cfg.risk)

    rows = []
    for symbol, data_symbol in cfg.instruments.items():
        df = provider.history(data_symbol)
        if len(df) < 60:
            rows.append({"symbol": symbol, "data_symbol": data_symbol,
                         "action": "NO_DATA", "score": None})
            continue
        sig = strategy.generate(df)
        price = float(df["Close"].iloc[-1])
        atr_val = float(atr_indicator(df).iloc[-1])
        direction = 1
        if sig.score >= cfg.strategy.min_entry_score:
            action = "BUY"
        elif cfg.strategy.allow_short and sig.score <= -cfg.strategy.min_entry_score:
            action = "SHORT"  # only on brokers that support shorting
            direction = -1
        elif sig.score <= cfg.strategy.exit_score:
            action = "SELL"
        else:
            action = "HOLD"
        rows.append({
            "symbol": symbol,
            "data_symbol": data_symbol,
            "action": action,
            "score": round(sig.score, 4),
            "price": round(price, 4),
            "atr": round(atr_val, 4),
            "suggested_stop": round(
                risk.stop_loss_price(price, atr_val, direction), 4
            ),
            "suggested_take_profit": round(
                risk.take_profit_price(price, atr_val, direction), 4
            ),
            "suggested_quantity": risk.position_size(
                args.equity, args.equity, price, atr_val
            ),
        })

    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "min_entry_score": cfg.strategy.min_entry_score,
        "exit_score": cfg.strategy.exit_score,
        "sizing_equity": args.equity,
        "disclaimer": "Signals are statistical opinions, not guarantees. "
                      "Trading involves risk of loss.",
        "signals": rows,
    }

    print(f"{'SYMBOL':<20} {'ACTION':<8} {'SCORE':>7} {'PRICE':>10} "
          f"{'STOP':>10} {'TP':>10}")
    for r in rows:
        if r["score"] is None:
            print(f"{r['symbol']:<20} {r['action']:<8}")
            continue
        print(f"{r['symbol']:<20} {r['action']:<8} {r['score']:>7.2f} "
              f"{r['price']:>10.2f} {r['suggested_stop']:>10.2f} "
              f"{r['suggested_take_profit']:>10.2f}")

    if args.out:
        if args.out.endswith(".csv"):
            fields = ["symbol", "data_symbol", "action", "score", "price", "atr",
                      "suggested_stop", "suggested_take_profit", "suggested_quantity"]
            with open(args.out, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                for r in rows:
                    writer.writerow({k: r.get(k) for k in fields})
        else:
            with open(args.out, "w") as fh:
                json.dump(payload, fh, indent=2)
        print(f"\nsignals written to {args.out}")
    return 0


def cmd_backtest(cfg: Config, args) -> int:
    from .backtest.engine import BacktestEngine, BacktestSettings
    from .strategies import build_default_ensemble

    regime_ok = None
    if args.csv_dir:
        from .data.market_data import load_csv_dir

        data = load_csv_dir(args.csv_dir)
        if cfg.regime.enabled and cfg.regime.symbol.upper() in data:
            regime_ok = _regime_series(data.pop(cfg.regime.symbol.upper()), cfg)
    else:
        from .data.market_data import MarketDataProvider

        provider = MarketDataProvider(interval=cfg.data.interval, lookback_days=args.days)
        data = provider.history_map(list(cfg.instruments.values()))
        if cfg.regime.enabled:
            bench = provider.history(cfg.regime.symbol)
            if len(bench) >= cfg.regime.sma_window:
                regime_ok = _regime_series(bench, cfg)
            else:
                print(f"warning: not enough {cfg.regime.symbol} data for the "
                      "regime filter — running without it", file=sys.stderr)

    if not data:
        print("No data available for the configured universe.", file=sys.stderr)
        return 1
    print(f"Backtesting {len(data)} instruments: {', '.join(sorted(data))}"
          + (" [regime filter ON]" if regime_ok is not None else "") + "\n")

    engine = BacktestEngine(
        strategy=build_default_ensemble(cfg.strategy.weights),
        risk_config=cfg.risk,
        settings=BacktestSettings(
            initial_cash=args.cash,
            min_entry_score=cfg.strategy.min_entry_score,
            exit_score=cfg.strategy.exit_score,
            allow_short=cfg.strategy.allow_short,
        ),
    )
    result = engine.run(data, regime_ok=regime_ok)
    print(result.summary())
    if args.trades:
        print("\nTrades:")
        for t in result.trades:
            side = "LONG " if t.direction > 0 else "SHORT"
            print(f"  {side} {t.symbol:<8} {t.entry_date.date()} -> {t.exit_date.date()} "
                  f"qty={t.quantity:g} {t.entry_price:.2f} -> {t.exit_price:.2f} "
                  f"pnl={t.pnl:+.2f} ({t.exit_reason})")
    if args.report:
        from .backtest.report import write_report

        write_report(result, args.report)
        print(f"\nHTML report written to {args.report}")
    return 0


def _regime_series(benchmark_df, cfg: Config):
    from .indicators import sma

    close = benchmark_df["Close"]
    return close >= sma(close, cfg.regime.sma_window)


def cmd_once(cfg: Config, _args) -> int:
    from .bot import TradingBot

    TradingBot(cfg).run_once()
    return 0


def cmd_run(cfg: Config, _args) -> int:
    from .bot import TradingBot

    mode = "DRY-RUN" if cfg.dry_run else cfg.broker.kind
    print(f"Starting quantbot [{mode}] — Ctrl+C to stop.")
    if not cfg.dry_run and cfg.is_real_money():
        print("*** REAL-MONEY TRADING. Losses are possible. ***")
    TradingBot(cfg).run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantbot", description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="path to config YAML")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("account", help="show broker account and positions")

    p_inst = sub.add_parser("instruments", help="search Trading 212 instruments")
    p_inst.add_argument("--search", default="", help="substring to match")
    p_inst.add_argument("--limit", type=int, default=25)

    p_sig = sub.add_parser("signals", help="compute and export current signals")
    p_sig.add_argument("--out", default="", help="write signals to .json or .csv")
    p_sig.add_argument("--equity", type=float, default=10_000.0,
                       help="equity used for suggested position sizes")

    p_bt = sub.add_parser("backtest", help="backtest the ensemble")
    p_bt.add_argument("--days", type=int, default=730, help="history length to fetch")
    p_bt.add_argument("--cash", type=float, default=10_000.0, help="initial cash")
    p_bt.add_argument("--csv-dir", default="", help="load OHLCV CSVs instead of Yahoo")
    p_bt.add_argument("--trades", action="store_true", help="print every trade")
    p_bt.add_argument("--report", default="", help="write a self-contained HTML report")

    sub.add_parser("once", help="run one trading cycle")
    sub.add_parser("run", help="run the trading loop")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    cfg = Config.load(args.config)

    handlers = {
        "account": cmd_account,
        "instruments": cmd_instruments,
        "signals": cmd_signals,
        "backtest": cmd_backtest,
        "once": cmd_once,
        "run": cmd_run,
    }
    return handlers[args.command](cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
