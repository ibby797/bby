import numpy as np
import pandas as pd
import pytest

from quantbot.backtest.engine import BacktestEngine, BacktestSettings
from quantbot.backtest import metrics
from quantbot.risk.manager import RiskConfig
from quantbot.strategies import SmaCrossover, build_default_ensemble
from tests.helpers import trending_down, trending_up, sideways


def run_engine(data, **settings_kwargs):
    engine = BacktestEngine(
        strategy=build_default_ensemble(),
        risk_config=RiskConfig(max_drawdown_pct=100.0),  # don't halt synthetic tests
        settings=BacktestSettings(**settings_kwargs),
    )
    return engine.run(data)


def test_backtest_produces_trades_and_equity():
    result = run_engine({"UP": trending_up(400), "DOWN": trending_down(400), "FLAT": sideways(400)})
    assert len(result.equity_curve) > 300
    assert result.stats["trades"] == len(result.trades) > 0
    assert result.stats["final_equity"] > 0
    # final equity equals initial + sum of all trade pnl (cash accounting closes out)
    assert result.stats["final_equity"] == pytest.approx(
        10_000.0 + sum(t.pnl for t in result.trades), rel=1e-9
    )


def test_uptrend_beats_downtrend():
    up = run_engine({"UP": trending_up(400)})
    down = run_engine({"DOWN": trending_down(400)})
    assert up.stats["final_equity"] > down.stats["final_equity"]
    # long-only bot in a persistent downtrend should mostly stay out / lose little
    assert down.stats["max_drawdown_pct"] < 20.0


def test_no_lookahead_signal_is_shifted():
    """The engine must act on yesterday's signal: a strategy that only fires on
    the very last bar can never produce a trade."""

    class LastBarOnly(SmaCrossover):
        name = "last_bar_only"

        def signal_series(self, df):
            s = pd.Series(0.0, index=df.index)
            s.iloc[-1] = 1.0
            return s

    engine = BacktestEngine(
        strategy=LastBarOnly(),
        risk_config=RiskConfig(max_drawdown_pct=100.0),
        settings=BacktestSettings(),
    )
    result = engine.run({"X": trending_up(200)})
    assert result.stats["trades"] == 0


def test_stops_limit_losses_on_crash():
    # A crash: stable then -60% collapse. The ATR stop must exit long before the bottom.
    n = 300
    closes = np.concatenate([np.full(150, 100.0), np.linspace(100, 40, n - 150)])
    from tests.helpers import make_ohlcv

    df = make_ohlcv(closes)
    result = run_engine({"CRASH": df})
    for t in result.trades:
        loss_pct = (t.exit_price / t.entry_price - 1) * 100
        assert loss_pct > -30  # stopped out, never rode it to -60%


def test_fees_and_slippage_reduce_returns():
    data = {"UP": trending_up(400)}
    cheap = run_engine(data, slippage_bps=0.0, fee_bps=0.0)
    costly = run_engine(data, slippage_bps=50.0, fee_bps=50.0)
    assert cheap.stats["final_equity"] > costly.stats["final_equity"]


def test_metrics_sane():
    equity = pd.Series(
        np.linspace(10_000, 12_000, 253),
        index=pd.bdate_range("2023-01-02", periods=253),
    )
    assert metrics.total_return_pct(equity) == pytest.approx(20.0)
    assert metrics.cagr_pct(equity) == pytest.approx(20.0, rel=0.05)
    assert metrics.max_drawdown_pct(equity) == 0.0
    assert metrics.sharpe_ratio(equity) > 0
    stats = metrics.trade_stats([100.0, -50.0, 30.0, -20.0])
    assert stats["trades"] == 4
    assert stats["win_rate_pct"] == 50.0
    assert stats["profit_factor"] == pytest.approx(130.0 / 70.0)


def test_summary_contains_disclaimer():
    result = run_engine({"UP": trending_up(300)})
    assert "does not guarantee" in result.summary()


def test_regime_filter_all_off_means_no_trades():
    data = {"UP": trending_up(300)}
    regime = pd.Series(False, index=data["UP"].index)
    engine = BacktestEngine(
        strategy=build_default_ensemble(),
        risk_config=RiskConfig(max_drawdown_pct=100.0),
        settings=BacktestSettings(),
    )
    result = engine.run(data, regime_ok=regime)
    assert result.stats["trades"] == 0
    assert result.stats["final_equity"] == pytest.approx(10_000.0)


def test_regime_filter_on_equals_no_filter():
    data = {"UP": trending_up(300)}
    regime = pd.Series(True, index=data["UP"].index)
    with_filter = run_engine(data)
    engine = BacktestEngine(
        strategy=build_default_ensemble(),
        risk_config=RiskConfig(max_drawdown_pct=100.0),
        settings=BacktestSettings(),
    )
    all_on = engine.run(data, regime_ok=regime)
    assert all_on.stats["final_equity"] == pytest.approx(
        with_filter.stats["final_equity"]
    )


def test_shorts_profit_in_downtrend():
    data = {"DOWN": trending_down(400)}
    long_only = run_engine(data)
    engine = BacktestEngine(
        strategy=build_default_ensemble(),
        risk_config=RiskConfig(max_drawdown_pct=100.0),
        settings=BacktestSettings(allow_short=True),
    )
    with_shorts = engine.run(data)
    short_trades = [t for t in with_shorts.trades if t.direction < 0]
    assert long_only.stats["trades"] == 0        # long-only sits out a downtrend
    assert len(short_trades) > 0                 # shorts engage it
    assert with_shorts.stats["final_equity"] > 10_000.0  # and profit from it
    # accounting invariant still holds with shorts in the mix
    assert with_shorts.stats["final_equity"] == pytest.approx(
        10_000.0 + sum(t.pnl for t in with_shorts.trades), rel=1e-9
    )


def test_short_stop_limits_losses_in_rally():
    # a short trapped in a melt-up must get stopped out, not ride it to ruin
    n = 300
    closes = np.concatenate([np.linspace(100, 70, 150), np.linspace(70, 200, n - 150)])
    from tests.helpers import make_ohlcv

    df = make_ohlcv(closes)
    engine = BacktestEngine(
        strategy=build_default_ensemble(),
        risk_config=RiskConfig(max_drawdown_pct=100.0),
        settings=BacktestSettings(allow_short=True),
    )
    result = engine.run({"TRAP": df})
    for t in result.trades:
        if t.direction < 0:
            loss_pct = (t.entry_price / t.exit_price - 1) * 100
            assert loss_pct > -30  # stopped, never rode a +100% rally short


def test_regime_gates_direction():
    # risk-off regime: longs blocked but shorts allowed
    data = {"DOWN": trending_down(400)}
    regime = pd.Series(False, index=data["DOWN"].index)
    engine = BacktestEngine(
        strategy=build_default_ensemble(),
        risk_config=RiskConfig(max_drawdown_pct=100.0),
        settings=BacktestSettings(allow_short=True),
    )
    result = engine.run(data, regime_ok=regime)
    assert all(t.direction < 0 for t in result.trades)
    assert len(result.trades) > 0


def test_html_report_renders(tmp_path):
    from quantbot.backtest.report import render_html, write_report

    result = run_engine({"UP": trending_up(300)})
    html_text = render_html(result)
    assert "<svg" in html_text
    assert "Sharpe" in html_text
    assert "does not guarantee" in html_text
    out = tmp_path / "report.html"
    write_report(result, str(out))
    assert out.read_text().startswith("<!DOCTYPE html>")
