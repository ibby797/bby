import numpy as np
import pandas as pd
import pytest

from t212bot import indicators as ind
from tests.helpers import trending_up


@pytest.fixture()
def df():
    return trending_up(300)


def test_sma_matches_manual(df):
    result = ind.sma(df["Close"], 10)
    expected = df["Close"].iloc[-10:].mean()
    assert result.iloc[-1] == pytest.approx(expected)
    assert result.iloc[:9].isna().all()


def test_rsi_bounds_and_direction(df):
    r = ind.rsi(df["Close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    # strongly rising series should have RSI above 50 on average
    assert r.mean() > 50


def test_rsi_all_gains_is_100():
    close = pd.Series(np.arange(1.0, 60.0))
    r = ind.rsi(close).dropna()
    assert (r == 100.0).all()


def test_macd_hist_is_macd_minus_signal(df):
    m = ind.macd(df["Close"]).dropna()
    assert np.allclose(m["hist"], m["macd"] - m["signal"])


def test_bollinger_pct_b_bounds_mid(df):
    b = ind.bollinger(df["Close"]).dropna()
    at_mid = (df["Close"] - b["mid"]).abs() < 1e-9
    assert ((b["upper"] - b["lower"]) > 0).all()
    assert b.loc[at_mid, "pct_b"].sub(0.5).abs().max() < 1e-9 if at_mid.any() else True


def test_atr_positive(df):
    a = ind.atr(df).dropna()
    assert (a > 0).all()


def test_donchian_excludes_current_bar():
    # Current bar making a new high must count as a breakout above `upper`.
    df = trending_up(100)
    ch = ind.donchian(df, 20)
    spike = df.copy()
    spike.loc[spike.index[-1], ["High", "Close"]] = df["High"].max() * 2
    ch_spike = ind.donchian(spike, 20)
    assert ch_spike["upper"].iloc[-1] == ch["upper"].iloc[-1]  # unchanged by today
    assert spike["Close"].iloc[-1] > ch_spike["upper"].iloc[-1]
