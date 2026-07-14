"""Volume confirmation: On-Balance Volume vs its own moving average.

Price trends backed by volume are more likely to persist. OBV above its SMA
(scaled by its rolling standard deviation) scores positive; distribution
(falling OBV) scores negative. This model rarely leads — its job is to confirm
or veto what the price-based models see.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import obv, sma
from .base import Strategy


class ObvTrend(Strategy):
    name = "obv_trend"

    def __init__(self, window: int = 20, norm_mult: float = 2.0):
        self.window = window
        self.norm_mult = norm_mult

    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        series = obv(df)
        baseline = sma(series, self.window)
        scale = series.rolling(self.window, min_periods=self.window).std(ddof=0)
        score = (series - baseline) / (self.norm_mult * scale.replace(0.0, np.nan))
        return self._clip(score)
