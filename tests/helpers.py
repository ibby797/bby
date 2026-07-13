"""Synthetic OHLCV generators for offline testing."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(
    closes: np.ndarray, start: str = "2023-01-02", volatility: float = 0.01
) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    rng = np.random.default_rng(42)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + volatility * rng.random(n))
    lows = np.minimum(opens, closes) * (1 - volatility * rng.random(n))
    index = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=index,
    )


def trending_up(n: int = 300, start_price: float = 100.0, drift: float = 0.003) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rets = drift + 0.006 * rng.standard_normal(n)
    closes = start_price * np.exp(np.cumsum(rets))
    return make_ohlcv(closes)


def trending_down(n: int = 300, start_price: float = 100.0, drift: float = -0.003) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rets = drift + 0.006 * rng.standard_normal(n)
    closes = start_price * np.exp(np.cumsum(rets))
    return make_ohlcv(closes)


def sideways(n: int = 300, price: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(13)
    closes = price + np.cumsum(0.3 * rng.standard_normal(n))
    closes = price + (closes - closes.mean()) * 0.5  # keep it range-bound
    return make_ohlcv(closes)
