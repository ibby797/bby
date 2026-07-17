"""Parameter grid search with an honest train/test split.

Walk-forward-style validation is the only defensible way to claim a parameter
change "increases wins": tune on the TRAIN window, then check the untouched
TEST window. A combination that shines in train and collapses in test is
overfit — the improvement was imaginary. Results are ranked by the train
metric but reported with test metrics side by side so the gap is visible.

The test slice keeps a warmup overlap (indicator history) with the train
slice; trades opened during warmup are counted, which slightly blurs the
boundary — acceptable for ranking purposes, noted here for honesty.
"""

from __future__ import annotations

import dataclasses
import itertools
from dataclasses import dataclass

import pandas as pd

from ..risk.manager import RiskConfig
from ..strategies.base import Strategy
from .engine import BacktestEngine, BacktestSettings

WARMUP_BARS = 60

DEFAULT_GRID = {
    "min_entry_score": [0.20, 0.25, 0.30],
    "atr_stop_multiplier": [2.0, 2.5, 3.0],
    "atr_take_profit_multiplier": [4.0, 5.0, 6.0],
}

# which dataclass each grid key belongs to
_SETTINGS_KEYS = {"min_entry_score", "exit_score", "allow_short"}


@dataclass
class GridResult:
    params: dict
    train: dict
    test: dict


def _split(data: dict[str, pd.DataFrame], train_fraction: float):
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    cut_idx = int(len(all_dates) * train_fraction)
    if cut_idx < WARMUP_BARS * 2 or len(all_dates) - cut_idx < WARMUP_BARS:
        raise ValueError(
            "not enough history to split: need at least "
            f"{WARMUP_BARS * 3} bars, got {len(all_dates)}"
        )
    cut = all_dates[cut_idx]
    test_start = all_dates[max(cut_idx - WARMUP_BARS, 0)]
    train = {s: df[df.index <= cut] for s, df in data.items()}
    test = {s: df[df.index >= test_start] for s, df in data.items()}
    train = {s: df for s, df in train.items() if len(df) >= WARMUP_BARS}
    test = {s: df for s, df in test.items() if len(df) >= WARMUP_BARS}
    return train, test


def grid_search(
    strategy: Strategy,
    data: dict[str, pd.DataFrame],
    grid: dict[str, list] | None = None,
    risk_base: RiskConfig | None = None,
    settings_base: BacktestSettings | None = None,
    train_fraction: float = 0.7,
    metric: str = "sharpe",
) -> list[GridResult]:
    """Run every grid combination on train and test windows.

    Returns results sorted by the TRAIN metric (descending) — read the test
    column before believing any of it.
    """
    grid = grid or DEFAULT_GRID
    risk_base = risk_base or RiskConfig()
    settings_base = settings_base or BacktestSettings()
    train_data, test_data = _split(data, train_fraction)

    keys = list(grid)
    results: list[GridResult] = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        risk = dataclasses.replace(
            risk_base, **{k: v for k, v in params.items() if k not in _SETTINGS_KEYS}
        )
        settings = dataclasses.replace(
            settings_base, **{k: v for k, v in params.items() if k in _SETTINGS_KEYS}
        )
        engine = BacktestEngine(strategy=strategy, risk_config=risk, settings=settings)
        try:
            train_stats = engine.run(train_data).stats
            test_stats = engine.run(test_data).stats
        except ValueError:
            continue  # slice too short for this combo
        results.append(GridResult(params=params, train=train_stats, test=test_stats))

    results.sort(key=lambda r: r.train.get(metric, 0.0), reverse=True)
    return results


def format_results(results: list[GridResult], metric: str = "sharpe", top: int = 10) -> str:
    if not results:
        return "no valid grid results"
    lines = [
        f"{'PARAMS':<52} {'TRAIN ' + metric:>12} {'train ret%':>10} "
        f"{'TEST ' + metric:>11} {'test ret%':>10} {'trades':>7}",
        "-" * 106,
    ]
    for r in results[:top]:
        params = ", ".join(f"{k}={v:g}" for k, v in r.params.items())
        lines.append(
            f"{params:<52} {r.train.get(metric, 0.0):>12.2f} "
            f"{r.train['total_return_pct']:>10.2f} "
            f"{r.test.get(metric, 0.0):>11.2f} "
            f"{r.test['total_return_pct']:>10.2f} "
            f"{r.train['trades'] + r.test['trades']:>7d}"
        )
    lines += [
        "-" * 106,
        "Ranked by TRAIN — judge by TEST. A big train/test gap means the",
        "'improvement' is overfitting, not edge. Past performance does not",
        "guarantee future results.",
    ]
    return "\n".join(lines)
