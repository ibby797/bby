"""Momentum: MACD line (fast EMA - slow EMA) as a fraction of price.

The MACD *histogram* measures momentum acceleration and oscillates around zero
even inside a steady trend, so it's a poor direction score. The MACD *line*
stays positive throughout an uptrend and negative in a downtrend, which is the
directional information we want. Score = (macd_line / close) / saturation,
clipped: with the default 1% saturation, the fast EMA sitting 1% above the
slow EMA (relative to price) yields full conviction.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import ema
from .base import Strategy


class MacdMomentum(Strategy):
    name = "macd_momentum"

    def __init__(self, fast: int = 12, slow: int = 26, saturation: float = 0.01):
        if fast >= slow:
            raise ValueError("fast span must be < slow span")
        self.fast = fast
        self.slow = slow
        self.saturation = saturation

    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        close = df["Close"]
        macd_line = ema(close, self.fast) - ema(close, self.slow)
        return self._clip((macd_line / close) / self.saturation)
