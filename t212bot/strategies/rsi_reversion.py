"""Mean-reversion: buy oversold, sell overbought, graded linearly.

RSI at ``50`` scores 0; RSI at ``50 - band`` (default 30) scores +1;
RSI at ``50 + band`` (default 70) scores -1.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import rsi
from .base import Strategy


class RsiReversion(Strategy):
    name = "rsi_reversion"

    def __init__(self, period: int = 14, band: float = 20.0):
        self.period = period
        self.band = band

    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        r = rsi(df["Close"], self.period)
        return self._clip((50.0 - r) / self.band)
