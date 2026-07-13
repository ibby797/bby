"""Weighted ensemble of strategies.

Combining decorrelated alpha models (trend + momentum + mean-reversion) is how
real systematic funds reduce single-model risk: when one model is wrong, the
others damp the position. The ensemble score is the weighted average of member
scores, still in [-1, 1].
"""

from __future__ import annotations

from typing import Mapping, Sequence

import pandas as pd

from .base import Signal, Strategy
from .bollinger_reversion import BollingerReversion
from .donchian_breakout import DonchianBreakout
from .macd_momentum import MacdMomentum
from .rsi_reversion import RsiReversion
from .sma_crossover import SmaCrossover


class EnsembleStrategy(Strategy):
    name = "ensemble"

    def __init__(self, members: Sequence[Strategy], weights: Mapping[str, float] | None = None):
        if not members:
            raise ValueError("ensemble needs at least one member strategy")
        self.members = list(members)
        weights = dict(weights or {})
        self.weights = {m.name: float(weights.get(m.name, 1.0)) for m in self.members}
        total = sum(abs(w) for w in self.weights.values())
        if total <= 0:
            raise ValueError("ensemble weights sum to zero")
        self._total_weight = total

    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        # Plain addition so NaN propagates: the ensemble abstains until every
        # member has enough history, instead of voting with partial opinions.
        combined = None
        for member in self.members:
            weighted = member.signal_series(df) * self.weights[member.name]
            combined = weighted if combined is None else combined + weighted
        return self._clip(combined / self._total_weight)

    def generate(self, df: pd.DataFrame) -> Signal:
        parts = [m.generate(df) for m in self.members]
        score_series = self.signal_series(df)
        if score_series.empty or pd.isna(score_series.iloc[-1]):
            return Signal(0.0, "ensemble: insufficient data")
        detail = ", ".join(p.reason for p in parts)
        score = float(score_series.iloc[-1])
        return Signal(score, f"ensemble={score:+.2f} [{detail}]")


# Trend/momentum models carry full weight; the two mean-reversion models get
# half weight so they temper entries at stretched prices without being able to
# fully veto a strong trend (they vote against it by construction).
DEFAULT_WEIGHTS = {
    "sma_crossover": 1.0,
    "donchian_breakout": 1.0,
    "macd_momentum": 1.0,
    "rsi_reversion": 0.5,
    "bollinger_reversion": 0.5,
}


def build_default_ensemble(weights: Mapping[str, float] | None = None) -> EnsembleStrategy:
    """The standard five-model ensemble: two trend, one momentum, two reversion."""
    merged = dict(DEFAULT_WEIGHTS)
    merged.update(weights or {})
    return EnsembleStrategy(
        members=[
            SmaCrossover(),
            DonchianBreakout(),
            MacdMomentum(),
            RsiReversion(),
            BollingerReversion(),
        ],
        weights=merged,
    )
