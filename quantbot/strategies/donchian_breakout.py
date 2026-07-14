"""Trend-following: position of the close within the Donchian channel.

A close at the top of the previous ``window``-bar range (or breaking above it)
scores +1; at the bottom scores -1. This is the classic turtle-style breakout
signal in graded form.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import donchian
from .base import Strategy


class DonchianBreakout(Strategy):
    name = "donchian_breakout"

    def __init__(self, window: int = 20):
        self.window = window

    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        ch = donchian(df, self.window)
        half_width = (ch["upper"] - ch["lower"]) / 2.0
        score = (df["Close"] - ch["mid"]) / half_width.replace(0.0, np.nan)
        return self._clip(score)
