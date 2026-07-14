"""Mean-reversion: position within the Bollinger band (%B).

Price at the lower band (%B = 0) scores +1, at the upper band (%B = 1) scores
-1, mid-band scores 0.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import bollinger
from .base import Strategy


class BollingerReversion(Strategy):
    name = "bollinger_reversion"

    def __init__(self, window: int = 20, num_std: float = 2.0):
        self.window = window
        self.num_std = num_std

    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        pct_b = bollinger(df["Close"], self.window, self.num_std)["pct_b"]
        return self._clip(1.0 - 2.0 * pct_b)
