import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.outcomes import geometry_error, simulate_outcome

from tradedesk.config.models import ChargeSchedule
from tradedesk.engine.signals import ExitPlan, Signal
from tradedesk.markets.costs import EquityCostModel


def sig(**changes):
    values = dict(
        id="one",
        scrip_code="one",
        symbol="one",
        setup="nr7_breakout",
        armed_on="2026-01-01",
        trigger=100,
        stop=95,
        t1=110,
        t2=115,
        atr=2,
        exit_plan=ExitPlan(partial_fraction=1.0),
    )
    return Signal(**(values | changes))


def bars(**changes):
    frame = pd.DataFrame(
        dict(
            open=[100.0, 103.0],
            high=[111.0, 111.0],
            low=[99.0, 101.0],
            close=[101.0, 110.0],
            volume=[100000, 100000],
        ),
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )
    for key, value in changes.items():
        frame[key] = value
    return frame


@pytest.mark.parametrize("entry,stop,target", [(100, 95, 99), (100, 100, 110), (np.nan, 95, 110)])
def test_invalid_geometry_cannot_count_as_a_win(entry, stop, target):
    assert geometry_error(entry, stop, target)


def test_entry_day_high_before_unknown_fill_is_not_a_target_win():
    costs = EquityCostModel(ChargeSchedule())
    frame = bars().iloc[:1]
    result = simulate_outcome(sig(), frame, frame.index[0], 100, 10, costs)
    assert result["status"] == "triggered_pending"
    result = simulate_outcome(sig(), bars(), frame.index[0], 100, 10, costs)
    assert result["strict_success"] is True
    assert result["sessions_to_outcome"] == 1
    assert result["net_r"] < result["gross_r"]


def test_target_touch_after_known_open_can_still_lose_after_costs():
    signal = sig(stop=99.9, t1=100.01)
    result = simulate_outcome(
        signal,
        bars(low=[99.95, 100]),
        "2026-01-02",
        100,
        1,
        EquityCostModel(ChargeSchedule()),
        entry_at_open=True,
    )
    assert result["target_hit"] is True
    assert result["strict_success"] is False and result["label"] == 0
    assert result["net_r"] < 0


def test_stop_wins_ambiguous_bar_and_gap_exit_is_gap_aware():
    costs = EquityCostModel(ChargeSchedule())
    result = simulate_outcome(sig(), bars(low=[94.0, 99.0]), "2026-01-02", 100, 10, costs)
    assert result["outcome"] == "stop" and result["label"] == 0
    result = simulate_outcome(
        sig(),
        bars(open=[100.0, 90.0], high=[102.0, 96.0], low=[99.0, 89.0]),
        "2026-01-02",
        100,
        10,
        costs,
    )
    assert result["outcome"] == "gap_stop"
    assert result["exit_price"] < 90


def test_profitable_time_exit_is_not_relabelled_as_target_success():
    signal = sig(exit_plan=ExitPlan(partial_fraction=1, time_stop_sessions=1, time_stop_min_r=1))
    result = simulate_outcome(
        signal,
        bars(high=[102.0, 104.0], close=[101.0, 103.0]),
        "2026-01-02",
        100,
        100,
        EquityCostModel(ChargeSchedule()),
    )
    assert result["outcome"] == "time_stop"
    assert result["net_profitable"] is True and result["label"] == 0
