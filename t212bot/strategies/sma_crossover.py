"""Trend-following: fast/slow simple moving average divergence.

Score scales with how far the fast SMA sits above/below the slow SMA relative
to price, saturating at ``saturation`` (default 2% divergence = full score).
"""

from __future__ import annotations

import pandas as pd

from ..indicators import sma
from .base import Strategy


class SmaCrossover(Strategy):
    name = "sma_crossover"

    def __init__(self, fast: int = 20, slow: int = 50, saturation: float = 0.02):
        if fast >= slow:
            raise ValueError("fast window must be < slow window")
        self.fast = fast
        self.slow = slow
        self.saturation = saturation

    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        close = df["Close"]
        divergence = (sma(close, self.fast) - sma(close, self.slow)) / close
        return self._clip(divergence / self.saturation)
