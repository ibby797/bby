"""Market data via Yahoo Finance (yfinance).

The Trading 212 public API deliberately provides no price feed, so signals are
computed from Yahoo OHLCV data. Instruments are configured as a mapping from
Trading 212 ticker (e.g. ``AAPL_US_EQ``) to Yahoo symbol (e.g. ``AAPL``).

Frames are normalized to columns Open/High/Low/Close/Volume with a tz-naive
DatetimeIndex, auto-adjusted for splits/dividends, and cached in memory with a
TTL so repeated bot cycles don't hammer the data source.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)
    df = df[[c for c in OHLCV_COLUMNS if c in df.columns]].copy()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.dropna(subset=["Close"])
    return df


class MarketDataProvider:
    def __init__(
        self,
        interval: str = "1d",
        lookback_days: int = 400,
        cache_ttl_seconds: float = 300.0,
    ):
        self.interval = interval
        self.lookback_days = lookback_days
        self.cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def history(self, yahoo_symbol: str) -> pd.DataFrame:
        """OHLCV history for one symbol; empty frame on failure (never raises)."""
        cached = self._cache.get(yahoo_symbol)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return cached[1]

        import yfinance as yf  # deferred so offline tests don't need it

        try:
            raw = yf.Ticker(yahoo_symbol).history(
                period=f"{self.lookback_days}d",
                interval=self.interval,
                auto_adjust=True,
            )
        except Exception as exc:  # network/parse errors must not kill the bot
            log.error("failed to fetch %s: %s", yahoo_symbol, exc)
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        df = _normalize(raw)
        if df.empty:
            log.warning("no data returned for %s", yahoo_symbol)
        else:
            self._cache[yahoo_symbol] = (time.monotonic(), df)
        return df

    def history_map(self, yahoo_symbols: list[str]) -> dict[str, pd.DataFrame]:
        out = {}
        for symbol in yahoo_symbols:
            df = self.history(symbol)
            if not df.empty:
                out[symbol] = df
        return out

    def latest_price(self, yahoo_symbol: str) -> float | None:
        df = self.history(yahoo_symbol)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])


def load_csv_dir(path: str | Path) -> dict[str, pd.DataFrame]:
    """Load OHLCV CSVs (one per symbol, filename = symbol, must contain a
    Date column plus Open/High/Low/Close/Volume) — offline backtesting input."""
    out: dict[str, pd.DataFrame] = {}
    for csv_path in sorted(Path(path).glob("*.csv")):
        df = pd.read_csv(csv_path)
        date_col = next(
            (c for c in df.columns if c.lower() in ("date", "datetime", "time")), None
        )
        if date_col is None:
            log.warning("skipping %s: no date column", csv_path.name)
            continue
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col)
        out[csv_path.stem.upper()] = _normalize(df)
    return out
