"""The cost model against hand calculations."""

import numpy as np
import pandas as pd
import pytest

from momo.config import ConfigError, CostsCfg
from momo.costs import CostModel, StatutorySchedule

from conftest import STATUTORY


@pytest.fixture(scope="module")
def schedule():
    return StatutorySchedule.load(STATUTORY)


def _model(schedule, dates, brokerage=10.0, k=0.5):
    cfg = CostsCfg(brokerage_bps_per_side=brokerage, spread_model="fixed", fixed_half_spread_bps=0.0,
                   impact_coefficient=k)
    return CostModel(schedule, cfg, pd.DatetimeIndex(dates))


def test_statutory_cost_of_one_crore_buy_and_sell_in_november_2024(schedule):
    m = _model(schedule, ["2024-11-04"])
    value = 1e7  # Rs 1 crore
    brokerage, stt, exchange, sebi, ipft = 10_000.0, 10_000.0, 297.0, 10.0, 10.0
    gst = 0.18 * (brokerage + exchange + sebi + ipft)
    buy = brokerage + stt + exchange + sebi + ipft + gst + 1_500.0  # stamp duty 0.015% on the buy only
    sell = brokerage + stt + exchange + sebi + ipft + gst
    bps = m.per_side_statutory_bps(0)
    assert bps["buy_bps"] == pytest.approx(buy / value * 1e4, rel=1e-9)
    assert bps["sell_bps"] == pytest.approx(sell / value * 1e4, rel=1e-9)

    zero = np.zeros(1)
    fc = m.fills(0, np.array([value]), zero, np.array([1e9]), np.array([0.0]), zero)
    assert fc.per_stock[0] == pytest.approx(buy, rel=1e-9)
    assert fc.by_component["stamp_duty"] == pytest.approx(1_500.0) and fc.by_component["gst"] == pytest.approx(gst)


def test_dated_schedule_changes_on_the_right_days(schedule):
    days = pd.DatetimeIndex(["2012-06-29", "2012-07-02", "2020-06-30", "2020-07-01", "2024-09-30", "2024-10-01",
                             "2017-06-30", "2017-07-03"])
    r = schedule.daily(days)
    assert r["stt_buy"][0] == 0.00125 and r["stt_buy"][1] == 0.001  # Finance Act 2012
    assert r["stamp_sell"][2] > 0 and r["stamp_sell"][3] == 0.0 and r["stamp_buy"][3] == 0.00015  # uniform stamp duty
    assert r["exchange"][4] == 0.0000322 and r["exchange"][5] == 0.0000297  # NSE flat rate
    assert r["service_tax"][6] == 0.15 and r["service_tax"][7] == 0.18  # service tax -> GST


def test_square_root_impact_and_spread(schedule):
    m = _model(schedule, ["2024-11-04"], brokerage=0.0, k=0.5)
    m.rates = {k: np.zeros(1) for k in m.rates}  # isolate spread + impact
    value, adv, vol, half_spread = np.array([4e6]), np.array([1e8]), np.array([0.02]), np.array([0.001])
    fc = m.fills(0, value, np.zeros(1), adv, vol, half_spread)
    expected_impact = 0.5 * 0.02 * np.sqrt(4e6 / 1e8) * 4e6  # k x vol x sqrt(participation) x value
    assert fc.by_component["impact"] == pytest.approx(expected_impact)
    assert fc.by_component["spread"] == pytest.approx(0.001 * 4e6)
    # four times the order -> twice the impact per rupee
    fc4 = m.fills(0, value * 4, np.zeros(1), adv, vol, half_spread)
    assert fc4.by_component["impact"] / (4 * value[0]) == pytest.approx(2 * expected_impact / value[0])


def test_costs_can_be_switched_off(schedule):
    cfg = CostsCfg(brokerage_bps_per_side=10, spread_model="fixed", fixed_half_spread_bps=5, impact_coefficient=1)
    m = CostModel(schedule, cfg, pd.DatetimeIndex(["2024-11-04"]), enabled=False)
    fc = m.fills(0, np.array([1e7]), np.array([0.0]), np.array([1e8]), np.array([0.02]), np.array([0.001]))
    assert fc.per_stock.sum() == 0.0


def test_date_before_the_schedule_is_an_error_not_a_guess(schedule):
    with pytest.raises(ConfigError, match="no 'stt_delivery' rate"):
        schedule.daily(pd.DatetimeIndex(["1999-01-04"]))


def test_unverified_entries_are_listed(schedule):
    assert any("stamp_duty" in u for u in schedule.unverified()), "entries marked `checked: no` must be surfaced"
