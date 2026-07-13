"""Strategy base classes.

Every strategy exposes a vectorized ``signal_series(df)`` returning a score in
[-1, +1] for each bar:

  * +1  = strongest possible buy conviction
  *  0  = neutral / no opinion
  * -1  = strongest possible sell/exit conviction

Trading 212 Invest/ISA accounts are long-only, so negative scores are used to
exit or avoid positions, never to short.

Vectorized series (instead of a point-in-time ``generate``) let the backtester
evaluate the strategy over history without O(n^2) recomputation, and the live
bot simply reads the final value.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Signal:
    score: float  # -1.0 .. +1.0
    reason: str = ""


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def signal_series(self, df: pd.DataFrame) -> pd.Series:
        """Return a score series in [-1, 1] aligned to ``df.index``.

        ``df`` is an OHLCV frame (columns Open/High/Low/Close/Volume). The
        value at index ``t`` may only use data up to and including bar ``t``
        (no lookahead).
        """

    def generate(self, df: pd.DataFrame) -> Signal:
        """Point-in-time signal for the most recent bar."""
        series = self.signal_series(df)
        if series.empty or pd.isna(series.iloc[-1]):
            return Signal(0.0, f"{self.name}: insufficient data")
        score = float(np.clip(series.iloc[-1], -1.0, 1.0))
        return Signal(score, f"{self.name}={score:+.2f}")

    @staticmethod
    def _clip(series: pd.Series) -> pd.Series:
        return series.clip(-1.0, 1.0)
