import pytest

from t212bot.risk.manager import RiskConfig, RiskManager


@pytest.fixture()
def rm():
    return RiskManager(RiskConfig())


def test_position_size_risk_math(rm):
    # equity 10_000, 1% risk = 100 at stake; ATR 2 * mult 2.5 = 5 stop distance
    # => 20 shares; value 20*50=1000 <= 20% cap (2000); cash ok.
    qty = rm.position_size(equity=10_000, free_cash=10_000, price=50.0, atr_value=2.0)
    assert qty == pytest.approx(20.0)


def test_position_size_capped_by_max_position_value(rm):
    # tiny ATR would suggest a huge position; the 20% value cap must bite:
    # 2000 / 50 = 40 shares max.
    qty = rm.position_size(equity=10_000, free_cash=10_000, price=50.0, atr_value=0.01)
    assert qty == pytest.approx(40.0)


def test_position_size_respects_cash_buffer(rm):
    # free cash 600, buffer 5% of 10k = 500 -> only 100 spendable -> 2 shares.
    qty = rm.position_size(equity=10_000, free_cash=600.0, price=50.0, atr_value=2.0)
    assert qty == pytest.approx(2.0)


def test_position_size_zero_when_invalid(rm):
    assert rm.position_size(10_000, 10_000, price=0.0, atr_value=2.0) == 0.0
    assert rm.position_size(10_000, 10_000, price=50.0, atr_value=0.0) == 0.0
    assert rm.position_size(10_000, 100.0, price=50.0, atr_value=2.0) == 0.0  # < buffer


def test_stops_and_take_profit(rm):
    assert rm.stop_loss_price(100.0, 2.0) == pytest.approx(95.0)
    assert rm.take_profit_price(100.0, 2.0) == pytest.approx(110.0)


def test_trailing_stop_only_ratchets_up(rm):
    stop = rm.stop_loss_price(100.0, 2.0)  # 95
    raised = rm.updated_trailing_stop(stop, highest_close=110.0, atr_value=2.0)
    assert raised == pytest.approx(105.0)
    # price falls back: stop must NOT move down
    unchanged = rm.updated_trailing_stop(raised, highest_close=100.0, atr_value=2.0)
    assert unchanged == raised


def test_daily_loss_kill_switch(rm):
    ks = rm.check_kill_switches(equity=9_690, peak_equity=10_000, day_start_equity=10_000)
    assert ks.halted and not ks.liquidate
    assert "daily loss" in ks.reason


def test_drawdown_kill_switch_liquidates(rm):
    ks = rm.check_kill_switches(equity=8_400, peak_equity=10_000, day_start_equity=8_500)
    assert ks.halted and ks.liquidate
    assert "drawdown" in ks.reason


def test_no_kill_switch_in_normal_conditions(rm):
    ks = rm.check_kill_switches(equity=9_900, peak_equity=10_000, day_start_equity=9_950)
    assert not ks.halted


def test_can_open_new_gates(rm):
    ok, _ = rm.can_open_new(open_positions=0, exposure_value=0, equity=10_000)
    assert ok
    ok, why = rm.can_open_new(open_positions=5, exposure_value=0, equity=10_000)
    assert not ok and "max open positions" in why
    ok, why = rm.can_open_new(open_positions=1, exposure_value=9_500, equity=10_000)
    assert not ok and "exposure" in why


def test_config_validation():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade_pct=50.0).validate()
    with pytest.raises(ValueError):
        RiskConfig(max_open_positions=0).validate()
