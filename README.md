# quantbot — broker-agnostic multi-strategy trading bot

One systematic trading engine, many venues: **Trading 212**, **Alpaca**, **100+
crypto exchanges via CCXT**, or the **built-in paper broker** that needs no
account at all. Six-model signal ensemble, volatility-adjusted sizing,
software-managed stops, market-regime filter, account-level kill switches, an
event-driven backtester that runs the exact same decision logic as the live
loop — and a `signals` export so even *unsupported* trading apps can use its
output. Ships as a normal Python package, a Docker image, or a **single
runnable file** (`quantbot.pyz`).

> ## ⚠️ Read this first — there is no such thing as guaranteed profit
>
> **No trading bot can guarantee winning.** Not this one, not any other.
> Markets are adversarial and substantially random; anyone promising
> guaranteed returns is lying to you (it's the single most common trading
> scam). What separates systematic trading from gambling is not
> win-guarantees — it's **risk control**: sizing positions so no single trade
> can hurt you, cutting losers automatically, and halting when the account
> draws down. That is what this bot implements.
>
> Trading involves risk of loss; crypto especially so. Backtests do not
> predict future performance. **Run paper/demo modes for months before
> considering real money**, start tiny, never trade money you can't afford to
> lose. Provided as-is, for educational purposes, no warranty. Automated
> trading may have tax and regulatory implications where you live — know them.

---

## Supported venues

| `broker:` | What | Paper mode | Real-money gate |
|---|---|---|---|
| `trading212` | Trading 212 Invest/ISA (stocks & ETFs) | `environment: demo` | `environment: live` + confirmation phrase |
| `alpaca` | Alpaca (US stocks & ETFs) | `brokers.alpaca.paper: true` | `paper: false` + confirmation phrase |
| `ccxt` | Binance, Kraken, Coinbase, Bybit, OKX… (spot crypto, `pip install ccxt`) | `brokers.ccxt.sandbox: true` | always real otherwise + confirmation phrase |
| `paper` | Built-in local simulator — no account, no keys, runs anywhere | always | never real money |

**Any other app**: `quantbot signals --out signals.json` (or `.csv`) exports
ranked BUY/SELL/HOLD calls with prices, suggested stops, take-profits, and
position sizes — pipe that into whatever platform you use, or read it yourself.

## What it does each cycle

1. **Syncs** equity, cash, and positions from the configured broker.
2. **Reconciles** its state with reality — positions you closed by hand are
   dropped, ones you opened by hand are adopted and given protective stops.
3. **Checks kill switches** — daily-loss halt; max-drawdown halt + liquidate.
4. **Manages exits** — ATR stop-loss/take-profit, upward-only trailing stop,
   ensemble sell signals; all as market orders.
5. **Checks the regime filter** — no new buying while the benchmark (default
   SPY) is below its 200-day average; one of the simplest, most effective
   drawdown reducers in systematic trading.
6. **Scans for entries** — ranks the universe by ensemble score, sizes by
   risk, buys the best candidates that pass every gate; a 24 h re-entry
   cooldown after any exit prevents whipsaw churn.
7. **Persists state** atomically, so restarts are safe.

### The strategy ensemble

Six decorrelated models vote, each scoring every instrument in `[-1, +1]`:

| Model | Type | Weight | Idea |
|---|---|---|---|
| `sma_crossover` | Trend | 1.0 | Fast vs slow SMA divergence, scaled by price |
| `donchian_breakout` | Trend | 1.0 | Position in / breakout from the 20-bar channel |
| `macd_momentum` | Momentum | 1.0 | MACD line (12/26 EMA spread) as a fraction of price |
| `rsi_reversion` | Mean reversion | 0.5 | Buy oversold, fade overbought (Wilder RSI) |
| `bollinger_reversion` | Mean reversion | 0.5 | %B position within the Bollinger bands |
| `obv_trend` | Volume | 0.75 | On-Balance Volume vs its own average — volume confirms |

The weighted average must clear `min_entry_score` to open and `exit_score` to
force-close. Blending opposing styles is deliberate: reversion tempers buying
into stretched prices, trend keeps the bot out of falling knives, volume
confirms or vetoes.

### Short selling (`allow_short: true`)

The bot can sell first and buy back later, profiting from falling prices —
**on venues that support it**: Alpaca (marginable US equities) and the built-in
paper broker. **Trading 212's Invest/ISA API is long-only** (its CFD product
isn't in the public API), and spot crypto can't short either; on those venues
the flag is ignored and the bot stays long-only.

Everything mirrors: shorts open when the ensemble score is strongly *negative*
(`score <= -min_entry_score`), the stop-loss sits *above* entry, the
take-profit *below*, the trailing stop ratchets *down*, and a buy signal
covers the short. With the regime filter on, direction must align with the
market: longs only while the benchmark is above its 200-day average, shorts
only while it's below. The backtester models shorts fully collateralized
(notional reserved from cash) with the same mirrored exits.

**Warning:** a long can lose at most 100%; a short's loss is theoretically
unlimited as price rises. The ATR stop and position sizing bound this in
practice, but gaps ignore stops. Leave `allow_short: false` unless you
understand and accept that.

### Risk management (the important part)

- **Volatility-adjusted sizing** — each trade risks a fixed % of equity to its
  ATR stop; volatile instruments automatically get smaller positions.
- **Hard caps** — max position value, max open positions, max total exposure,
  minimum cash buffer.
- **Protective exits** — ATR stop-loss/take-profit, ratchet-only trailing stop.
- **Breakeven stop** — once a trade is +1R in profit the stop moves to entry:
  trades that were winners can no longer become full losers.
- **Partial profit-taking** — half the position banks at the first target
  (half the take-profit distance); the rest rides the trailing stop. Raises
  win rate and smooths the equity curve at the cost of capping part of each
  big winner.
- **Time exit** — positions going nowhere for 45 days are closed; dead
  capital blocks better entries.
- **Re-entry cooldown** and **market-regime filter** (see above).
- **Kill switches** — max daily loss halts entries; max drawdown from peak
  halts everything and liquidates.

### Measuring what helps: `optimize`

"More wins" claims are cheap; evidence is not. `quantbot optimize` grid-searches
the key parameters (entry threshold, stop and take-profit multiples) with a
**train/test split**: combinations are tuned on the first ~70% of history and
judged on the untouched remainder — the same idea as
[walk-forward analysis](https://en.wikipedia.org/wiki/Walk_forward_optimization),
the standard defense against overfitting. A parameter set that shines in train
and collapses in test is a mirage; the report shows both columns so you can
tell. Note that **win rate is not the goal** — a 95% win rate with one
catastrophic loser is worse than 45% with big winners. Judge by expectancy,
Sharpe, and max drawdown.

```bash
python run_bot.py optimize --days 1095            # 3y of data, default grid
python run_bot.py optimize --csv-dir ./data --metric expectancy
```

**Why software stops?** Market orders are the only order type every venue
supports (and the only type Trading 212's live API accepts), so the bot keeps
stop levels itself and fires a market sell when they're breached — checked
once per polling cycle. Fills can be worse than the stop in fast markets; size
accordingly.

### Market data

Brokers' public APIs rarely include a good price feed, so OHLCV comes from
Yahoo Finance (`yfinance`). The universe maps broker symbol → Yahoo symbol
(`AAPL_US_EQ: AAPL`, `BTC/USDT: BTC-USD`). Offline CSVs work for backtesting.

---

## Install — pick any of three ways

**1. Normal checkout** (Python 3.10+):

```bash
pip install -r requirements.txt        # + `pip install ccxt` for crypto
python run_bot.py --help
```

**2. Single file** — build once, copy `quantbot.pyz` anywhere (server, laptop,
Raspberry Pi), run it with any Python 3.10+ that has the deps installed:

```bash
python make_bundle.py                  # -> quantbot.pyz (~45 KiB)
python quantbot.pyz --help
```

**3. Docker** — runs anywhere Docker runs:

```bash
cp config.example.yaml config.yaml     # edit; set state_path: /app/state/state.json
echo "T212_API_KEY=..." > .env
docker compose up -d --build && docker compose logs -f
```

Also pip-installable as a package: `pip install .` gives you a `quantbot`
command.

### First-time Trading 212 connection (easiest way)

```bash
python run_bot.py setup
```

The wizard asks for your API key (Trading 212 app → switch to **Practice**
account → Settings → API (Beta) → Generate), saves a safe default config
(demo + dry-run), and immediately tests the connection. Then
`python run_bot.py run` starts the bot.

### Credentials (advanced: environment variables)

Environment variables take precedence over the config file and keep keys out
of files entirely — preferred for servers/Docker:

| Broker | Variables |
|---|---|
| Trading 212 | `T212_API_KEY` (app → Settings → API (Beta)) |
| Alpaca | `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` |
| CCXT | `CCXT_API_KEY`, `CCXT_SECRET` |
| paper | none |

## Usage

```bash
# Instant start, zero accounts: paper broker + dry-run off
printf 'broker: paper\ndry_run: false\n' > config.yaml
python run_bot.py once

# Current signals for the whole universe -> terminal + machine-readable file
python run_bot.py signals --out signals.json     # or signals.csv

# Broker account state
python run_bot.py account

# Find exact Trading 212 tickers
python run_bot.py instruments --search apple

# Backtest (2 years of Yahoo data) with a self-contained HTML report
python run_bot.py backtest --days 730 --trades --report report.html

# Backtest offline from your own OHLCV CSVs (Date,Open,High,Low,Close,Volume)
python run_bot.py backtest --csv-dir ./data --report report.html

# The full loop: poll, decide, execute, repeat
python run_bot.py run
```

## The safety ladder

The bot refuses to skip rungs. Climb them in order:

1. **Dry-run** (`dry_run: true` — the default): orders are logged, nothing is
   sent. Watch what it *would* do.
2. **Paper money**: built-in `paper` broker, Trading 212 demo, Alpaca paper,
   or a CCXT sandbox. Run for weeks; compare against the backtest.
3. **Real money** — requires `dry_run: false` on a real-money broker config
   **and** typing `confirm_live: I_UNDERSTAND_REAL_MONEY_IS_AT_RISK` into the
   config file. There is no env-var shortcut. Start with money you could lose
   entirely without pain.

## Configuration

Everything lives in `config.yaml` — see `config.example.yaml` for the fully
annotated reference: broker selection, universe, ensemble weights/thresholds,
regime filter, all risk limits, polling schedule, optional webhook
notifications. `QUANTBOT_BROKER`, `QUANTBOT_ENV`, and `QUANTBOT_DRY_RUN` env
vars override the config for containerized deployments.

## Architecture

```
run_bot.py / quantbot.pyz    CLI entry points
quantbot/
├── cli.py               account / instruments / signals / backtest / once / run
├── brokers/             Broker ABC + adapters: trading212, alpaca, ccxt, paper
├── api/client.py        Trading 212 REST client (rate limits, retries, 429s)
├── data/market_data.py  Yahoo OHLCV provider + CSV loader, TTL cache
├── indicators.py        SMA, EMA, RSI, MACD, Bollinger, ATR, Donchian, OBV
├── strategies/          6 alpha models + weighted ensemble (vectorized, no lookahead)
├── risk/manager.py      Sizing, stops, caps, cooldowns, kill switches
├── backtest/            Event-driven engine + metrics + HTML report (inline SVG)
├── execution/           Dry-run gate over any broker
├── bot.py               The live orchestrator loop (+ regime filter)
├── config.py            YAML + env config with real-money confirmation gate
├── state.py             Atomic JSON persistence of stops, cooldowns, equity peaks
└── notify.py            Optional webhook notifications
```

The backtester executes signals decided on bar *t−1* at bar *t*'s open with
slippage and fees, checks stops against intrabar lows (gaps fill at the open,
i.e. worse), honors the same regime filter and re-entry cooldown, and shares
the `RiskManager` with the live bot — a backtest is a faithful rehearsal, not
a fantasy.

## Tests & CI

```bash
python -m pytest tests/ -q     # 92 tests
```

Covers indicators, every model's behavior in up/down/sideways markets, sizing
math, kill switches, backtest accounting (final equity must equal initial cash
plus the sum of all trade PnL — shorts included), a no-lookahead guard,
regime-filter on/off equivalence and direction gating, mirrored short stops
and trailing stops, short-trapped-in-a-rally loss bounding, the paper broker's
ledger and short collateral, the real-money confirmation gates for every
broker, HTML report rendering, and full bot cycles against a fake broker —
including short entry, cover-on-stop, and refusing to short on long-only
venues. GitHub Actions runs the suite and builds the `.pyz` bundle on every
push.

## Known limitations — read before going live

- **Stops are polled, not exchange-side.** Overnight gaps and fast crashes
  fill below your stop level.
- **Daily bars by default.** Intraday intervals are configurable but Yahoo
  data quality/delays vary; this is not an HFT system and never will be over
  public REST APIs.
- **Yahoo data is unofficial** and occasionally wrong or late. The bot fails
  safe (skips instruments without data), but garbage in, garbage out.
- **No leverage; shorting only where the venue allows it** (Alpaca, paper).
  On long-only venues the correct bear-market output is mostly *cash* — the
  regime filter enforces exactly that. Short losses are theoretically
  unbounded; the stop and sizing cap them in practice but gaps ignore stops.
- **CCXT spot accounts have no entry-price memory**; adopted crypto positions
  use the current price as a stand-in average.
- FX/spread costs are approximated in backtests via `fee_bps`, not modeled
  precisely; the market-hours filter is a coarse UTC window.

## Sources

- [Trading 212 Public API documentation](https://docs.trading212.com/api)
- [Trading 212 API key help article](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key)
- [Alpaca API reference](https://docs.alpaca.markets/reference)
- [CCXT documentation](https://docs.ccxt.com/)

---

## Other projects in this repo

- **[clipfarm/](clipfarm/)** — 🎬 ClipFarm: paste a long-form YouTube video, auto-detect viral moments, batch-render up to 100 vertical captioned clips, and drip-post them to TikTok for content-rewards campaigns. See [clipfarm/README.md](clipfarm/README.md).
