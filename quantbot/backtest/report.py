"""Self-contained HTML backtest report: stats, equity curve, drawdown, trades.

No external assets — charts are inline SVG — so the file opens anywhere,
including on a phone. Light/dark friendly via prefers-color-scheme.
"""

from __future__ import annotations

import html

import pandas as pd

from .engine import BacktestResult


def _svg_line(
    series: pd.Series,
    width: int = 860,
    height: int = 240,
    stroke: str = "#2563eb",
    fill: str = "none",
    baseline: float | None = None,
) -> str:
    values = series.dropna()
    if len(values) < 2:
        return "<p>not enough data to chart</p>"
    lo, hi = float(values.min()), float(values.max())
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    span = (hi - lo) or 1.0
    pad = 8
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = pad + i * (width - 2 * pad) / (n - 1)
        y = pad + (hi - float(v)) * (height - 2 * pad) / span
        points.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(points)
    path = f'<polyline points="{poly}" fill="none" stroke="{stroke}" stroke-width="2"/>'
    if fill != "none":
        first_x, last_x = points[0].split(",")[0], points[-1].split(",")[0]
        area = (
            f'<polygon points="{first_x},{height - pad} {poly} {last_x},{height - pad}" '
            f'fill="{fill}" stroke="none" opacity="0.25"/>'
        )
        path = area + path
    labels = (
        f'<text x="{pad}" y="{height - 2}" class="axis">{values.index[0].date()}</text>'
        f'<text x="{width - pad}" y="{height - 2}" class="axis" text-anchor="end">'
        f"{values.index[-1].date()}</text>"
        f'<text x="{pad}" y="{pad + 4}" class="axis">{hi:,.0f}</text>'
        f'<text x="{pad}" y="{height - pad}" class="axis">{lo:,.0f}</text>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'style="max-width:100%;height:auto">{path}{labels}</svg>'
    )


def render_html(result: BacktestResult, title: str = "quantbot backtest report") -> str:
    s = result.stats
    equity = result.equity_curve
    peak = equity.cummax()
    drawdown_pct = (equity - peak) / peak * 100.0

    stat_rows = [
        ("Period", f"{s['start']} → {s['end']} ({s['bars']} bars)"),
        ("Initial equity", f"{s['initial_equity']:,.2f}"),
        ("Final equity", f"{s['final_equity']:,.2f}"),
        ("Total return", f"{s['total_return_pct']:.2f}%"),
        ("CAGR", f"{s['cagr_pct']:.2f}%"),
        ("Sharpe ratio", f"{s['sharpe']:.2f}"),
        ("Sortino ratio", f"{s['sortino']:.2f}"),
        ("Max drawdown", f"{s['max_drawdown_pct']:.2f}%"),
        ("Trades", str(s["trades"])),
        ("Win rate", f"{s['win_rate_pct']:.2f}%"),
        ("Profit factor", f"{s['profit_factor']:.2f}"),
        ("Expectancy / trade", f"{s['expectancy']:.2f}"),
    ]
    stats_html = "".join(
        f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
        for k, v in stat_rows
    )

    trade_rows = "".join(
        f"<tr><td>{html.escape(t.symbol)}</td>"
        f"<td>{'long' if t.direction > 0 else 'short'}</td>"
        f"<td>{t.entry_date.date()}</td><td>{t.exit_date.date()}</td>"
        f"<td>{t.quantity:g}</td><td>{t.entry_price:.2f}</td>"
        f"<td>{t.exit_price:.2f}</td>"
        f"<td class=\"{'pos' if t.pnl >= 0 else 'neg'}\">{t.pnl:+,.2f}</td>"
        f"<td>{html.escape(t.exit_reason)}</td></tr>"
        for t in result.trades[:500]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; --pos: #16a34a; --neg: #dc2626; }}
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 920px;
          padding: 0 1rem; }}
  h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .35rem .6rem; border-bottom: 1px solid
            color-mix(in srgb, currentColor 15%, transparent); }}
  .stats th {{ width: 40%; font-weight: 600; }}
  .pos {{ color: var(--pos); }} .neg {{ color: var(--neg); }}
  .axis {{ font-size: 11px; fill: currentColor; opacity: .6; }}
  .disclaimer {{ margin-top: 2rem; padding: .8rem 1rem; border-left: 4px solid var(--neg);
                 background: color-mix(in srgb, currentColor 6%, transparent); }}
  .tablewrap {{ overflow-x: auto; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<h2>Summary</h2>
<table class="stats">{stats_html}</table>
<h2>Equity curve</h2>
{_svg_line(equity, stroke="#2563eb", fill="#2563eb")}
<h2>Drawdown (%)</h2>
{_svg_line(drawdown_pct, stroke="#dc2626", fill="#dc2626", baseline=0.0)}
<h2>Trades ({len(result.trades)}{", first 500 shown" if len(result.trades) > 500 else ""})</h2>
<div class="tablewrap">
<table>
<tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>Qty</th><th>Entry px</th>
<th>Exit px</th><th>PnL</th><th>Reason</th></tr>
{trade_rows}
</table>
</div>
<p class="disclaimer"><strong>Past performance does not guarantee future
results.</strong> Backtests are simulations with assumptions (fills, slippage,
fees, data quality) that reality will violate. Nothing here is financial
advice.</p>
</body>
</html>
"""


def write_report(result: BacktestResult, path: str, title: str = "quantbot backtest report") -> None:
    with open(path, "w") as fh:
        fh.write(render_html(result, title=title))
