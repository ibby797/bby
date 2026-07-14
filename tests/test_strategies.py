import pandas as pd
import pytest

from quantbot.strategies import (
    BollingerReversion,
    DonchianBreakout,
    EnsembleStrategy,
    MacdMomentum,
    ObvTrend,
    RsiReversion,
    SmaCrossover,
    build_default_ensemble,
)
from tests.helpers import sideways, trending_down, trending_up

ALL_STRATEGIES = [
    SmaCrossover(),
    RsiReversion(),
    MacdMomentum(),
    BollingerReversion(),
    DonchianBreakout(),
    ObvTrend(),
]


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.name)
@pytest.mark.parametrize("data_fn", [trending_up, trending_down, sideways])
def test_scores_bounded(strategy, data_fn):
    scores = strategy.signal_series(data_fn(300)).dropna()
    assert not scores.empty
    assert ((scores >= -1.0) & (scores <= 1.0)).all()


def test_trend_strategies_like_uptrends():
    up = trending_up(300)
    down = trending_down(300)
    for strategy in (SmaCrossover(), DonchianBreakout(), MacdMomentum()):
        assert strategy.signal_series(up).dropna().tail(50).mean() > 0.2
        assert strategy.signal_series(down).dropna().tail(50).mean() < -0.2


def test_obv_confirms_direction():
    assert ObvTrend().signal_series(trending_up(300)).dropna().tail(50).mean() > 0.2
    assert ObvTrend().signal_series(trending_down(300)).dropna().tail(50).mean() < -0.2


def test_rsi_reversion_fades_trends():
    # mean-reversion should lean AGAINST a strong uptrend
    up = trending_up(300)
    assert RsiReversion().signal_series(up).dropna().tail(50).mean() < 0


def test_generate_insufficient_data():
    tiny = trending_up(5)
    for strategy in ALL_STRATEGIES:
        sig = strategy.generate(tiny)
        assert sig.score == 0.0
        assert "insufficient" in sig.reason


def test_ensemble_weighted_average():
    up = trending_up(300)
    members = [SmaCrossover(), DonchianBreakout()]
    ens = EnsembleStrategy(members, weights={"sma_crossover": 1.0, "donchian_breakout": 1.0})
    combined = ens.signal_series(up)
    manual = (members[0].signal_series(up) + members[1].signal_series(up)) / 2.0
    pd.testing.assert_series_equal(combined.dropna(), manual.dropna().clip(-1, 1))


def test_ensemble_zero_weights_rejected():
    with pytest.raises(ValueError):
        EnsembleStrategy([SmaCrossover()], weights={"sma_crossover": 0.0})


def test_default_ensemble_has_six_members():
    ens = build_default_ensemble()
    assert len(ens.members) == 6
    sig = ens.generate(trending_up(300))
    assert -1.0 <= sig.score <= 1.0
    assert "ensemble" in sig.reason
