# t212bot — systematic multi-strategy trading bot for Trading 212

An automated trading system for the [Trading 212 public API](https://docs.trading212.com/api):
a five-model signal ensemble, volatility-adjusted position sizing, software-managed
stop-losses, account-level kill switches, and an event-driven backtester that runs the
exact same decision logic as the live loop.

> ## ⚠️ Read this first — there is no such thing as guaranteed profit
>
> **No trading bot can guarantee winning.** Not this one, not any other. Markets are
> adversarial and substantially random; anyone promising guaranteed returns is lying to
> you (and it's the single most common trading scam). What separates serious systematic
> trading from gambling is not win-guarantees — it's **risk control**: sizing positions
> so no single trade can hurt you, cutting losers automatically, and halting when the
> account draws down. That is what this bot implements.
>
> Trading involves risk of loss. Backtest results do not predict future performance.
> **Run it on the demo (paper) environment until you have months of evidence**, start
> tiny if you ever go live, and never trade money you cannot afford to lose. This
> software is provided as-is, for educational purposes, with no warranty. Automated
> trading may also have tax and regulatory implications where you live — know them.

---

## What it does

Every polling cycle the bot:

1. **Syncs** cash, equity, and open positions from your Trading 212 account.
2. **Reconciles** its state with reality — positions you closed by hand are dropped,
   positions you opened by hand are adopted and given protective stops.
3. **Checks kill switches** — a daily loss beyond the limit halts new entries for the
   day; a drawdown beyond the limit halts everything and liquidates.
4. **Manages exits** — ATR stop-loss, ATR take-profit, upward-only trailing stop, and
   ensemble sell signals, all executed as market orders.
5. **Scans for entries** — ranks the whole universe by ensemble score, then sizes and
   buys the best candidates that pass every risk gate.
6. **Persists state** atomically to `state.json` so restarts are safe.

### The strategy ensemble

Five decorrelated models vote, each scoring every instrument in `[-1, +1]`:

| Model | Type | Idea |
|---|---|---|
| `sma_crossover` | Trend | Fast SMA vs slow SMA divergence, scaled by price |
| `donchian_breakout` | Trend | Position in / breakout from the 20-bar price channel |
| `macd_momentum` | Momentum | MACD line (12/26 EMA spread) as a fraction of price |
| `rsi_reversion` | Mean reversion | Buy oversold, fade overbought (Wilder RSI) |
| `bollinger_reversion` | Mean reversion | %B position within the Bollinger bands |

The weighted average (trend/momentum at full weight, reversion at half weight by
default) must clear `min_entry_score` to open and `exit_score` to force-close. Blending
opposing styles is deliberate: reversion models temper buying into stretched prices,
trend models keep the bot out of falling knives.

### Risk management (the important part)

- **Volatility-adjusted sizing** — each trade risks a fixed % of equity between entry
  and its ATR-based stop, so volatile instruments automatically get smaller positions.
- **Hard caps** — max position value, max open positions, max total exposure, minimum
  cash buffer.
- **Protective exits** — ATR stop-loss and take-profit, trailing stop that only ratchets
  up.
- **Re-entry cooldown** — after any exit, that ticker is untouchable for a configurable
  period (default 24 h) to prevent whipsaw churn.
- **Kill switches** — max daily loss (halts entries) and max drawdown from peak equity
  (halts everything and liquidates).

**Why software stops?** The live Trading 212 API accepts *market orders only* (limit /
stop orders work on demo). The bot therefore keeps stop levels itself and fires a market
sell when they're breached — checked once per polling cycle, so fills can be worse than
the stop level in fast markets. This is a real limitation; size accordingly.

### Market data

The Trading 212 API intentionally provides no price feed, so OHLCV data comes from
Yahoo Finance (`yfinance`). The universe is a mapping of Trading 212 ticker → Yahoo
symbol in the config.

---

## Setup

Requires Python 3.10+.

```bash
git clone <this repo> && cd bby
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Get an API key** (Invest and Stocks ISA accounts only; the API is in beta):
Trading 212 app → **Settings → API (Beta)** → generate a key. Practice-account keys
work against the demo environment — start there.

```bash
export T212_API_KEY="your-key-here"   # never put the key in config files
cp config.example.yaml config.yaml    # config.yaml is gitignored
```

## Usage

```bash
# Verify connectivity: account info, cash, positions
python run_bot.py account

# Find exact Trading 212 tickers for your universe
python run_bot.py instruments --search apple

# Backtest the ensemble on your configured universe (2 years of Yahoo data)
python run_bot.py backtest --days 730 --trades

# Backtest offline from your own OHLCV CSVs (Date,Open,High,Low,Close,Volume)
python run_bot.py backtest --csv-dir ./data

# One decision cycle (respects dry_run) — good for cron
python run_bot.py once

# The full loop: poll, decide, execute, repeat
python run_bot.py run
```

## The safety ladder

The bot refuses to skip rungs. Climb them in order:

1. **Demo + dry-run** (`environment: demo`, `dry_run: true` — the defaults). Orders are
   logged, nothing is sent. Watch what it *would* do.
2. **Demo + real orders** (`dry_run: false`). Paper money trades on Trading 212's demo
   environment. Run this for weeks. Compare results against the backtest.
3. **Live** — requires `environment: live`, `dry_run: false`, **and** typing
   `confirm_live: I_UNDERSTAND_REAL_MONEY_IS_AT_RISK` into the config file. There is no
   env-var shortcut. Start with money you could lose entirely without pain.

## Configuration

Everything lives in `config.yaml` (see `config.example.yaml` for the annotated
reference): universe, ensemble weights and thresholds, all risk limits, polling
schedule, and an optional Discord/Slack-compatible `webhook_url` for trade
notifications. `T212_ENV` and `T212_DRY_RUN` environment variables override the
config for containerized deployments.

## Architecture

```
run_bot.py               CLI (account / instruments / backtest / once / run)
t212bot/
├── api/client.py        REST client: auth, per-endpoint rate limiting, retries, 429 handling
├── data/market_data.py  Yahoo Finance OHLCV provider + CSV loader, TTL cache
├── indicators.py        SMA, EMA, RSI, MACD, Bollinger, ATR, Donchian (pure pandas)
├── strategies/          5 alpha models + weighted ensemble (vectorized, no lookahead)
├── risk/manager.py      Sizing, stops, exposure caps, cooldowns, kill switches
├── backtest/            Event-driven engine + Sharpe/Sortino/drawdown/trade metrics
├── execution/           Dry-run gate + market-order execution
├── bot.py               The live orchestrator loop
├── config.py            YAML + env config with live-trading confirmation gate
├── state.py             Atomic JSON persistence of stops, cooldowns, equity peaks
└── notify.py            Optional webhook notifications
```

The backtester executes signals decided on bar *t−1* at bar *t*'s open with
configurable slippage and fees, checks stops against intrabar lows (gaps fill at the
open, i.e. worse), and shares the `RiskManager` with the live bot — so a backtest is a
faithful rehearsal, not a fantasy.

## Tests

```bash
python -m pytest tests/ -q
```

58 tests cover the indicators, every strategy's behavior in up/down/sideways markets,
sizing math, kill switches, backtest accounting (final equity must equal initial cash
plus the sum of all trade PnL), a no-lookahead guard, and full bot cycles against a
fake broker — including stop-loss firing, dry-run isolation, and external-position
reconciliation.

## Known limitations — read before going live

- **Stops are polled, not exchange-side** (live API is market-order-only). Overnight
  gaps and fast crashes will fill below your stop level.
- **Daily bars by default.** Intraday intervals are configurable but Yahoo data quality
  and delays vary; this is not an HFT system and never will be over a public REST API.
- **Yahoo data is unofficial** and occasionally wrong or late. The bot fails safe
  (skips instruments with no data) but garbage in, garbage out.
- **No shorting, no leverage** — T212 Invest/ISA accounts are long-only; in a bear
  market the correct output of this bot is mostly *cash*.
- **FX costs** on non-base-currency instruments are approximated in backtests via
  `fee_bps`, not modeled precisely.
- The market-hours filter is a coarse UTC window (no holiday calendar, DST edges).

## Sources

- [Trading 212 Public API documentation](https://docs.trading212.com/api)
- [Trading 212 API — general information](https://docs.trading212.com/api/section/general-information)
- [Trading 212 API key help article](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key)
- [Trading 212 API docs (Redocly mirror)](https://t212public-api-docs.redoc.ly/)
