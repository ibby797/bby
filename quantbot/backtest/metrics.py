"""Performance metrics computed from an equity curve and a trade list."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def total_return_pct(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    return (equity.iloc[-1] / equity.iloc[0] - 1.0) * 100.0


def cagr_pct(equity: pd.Series, periods_per_year: int = 252) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return 0.0
    years = (len(equity) - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return ((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) * 100.0


def sharpe_ratio(equity: pd.Series, periods_per_year: int = 252, risk_free: float = 0.0) -> float:
    rets = equity.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    excess = rets - risk_free / periods_per_year
    std = excess.std(ddof=1)
    if std == 0 or math.isnan(std):
        return 0.0
    return float(excess.mean() / std * np.sqrt(periods_per_year))


def sortino_ratio(equity: pd.Series, periods_per_year: int = 252, risk_free: float = 0.0) -> float:
    rets = equity.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    excess = rets - risk_free / periods_per_year
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf")
    dd = np.sqrt((downside**2).mean())
    if dd == 0:
        return 0.0
    return float(excess.mean() / dd * np.sqrt(periods_per_year))


def max_drawdown_pct(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    running_peak = equity.cummax()
    drawdown = (equity - running_peak) / running_peak
    return float(-drawdown.min() * 100.0)


def trade_stats(pnls: list[float]) -> dict:
    if not pnls:
        return {
            "trades": 0, "win_rate_pct": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": len(pnls),
        "win_rate_pct": len(wins) / len(pnls) * 100.0,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": sum(pnls) / len(pnls),
    }
