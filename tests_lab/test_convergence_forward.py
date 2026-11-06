"""convergence_forward: only new sessions count, nothing is rewritten, the bar waits for closes."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from tradedesk_lab import convergence_forward as fw
from tradedesk_lab import new_listings as nl
from tradedesk_lab import results_reading as rr

NOW = datetime(2027, 3, 1, 21, 0)
DAYS = [d for d in pd.date_range("2026-08-03", periods=240, freq="B").date]
FIRST = DAYS.index(date(2026, 11, 9))


def _measure(stop: int | None = None, opening: float = 100.0) -> SimpleNamespace:
    days = DAYS if stop is None else DAYS[:stop]
    frame = pd.DataFrame({"S": [opening] * len(days)}, index=days)
    panels = nl.Panels(frame, frame, frame * 1.1, frame * 0 + 1e6)

    def call(code: str, e: int, exit_rule: str, short: bool = False):  # noqa: ANN202, FBT001, FBT002
        x = e + int(exit_rule) - 1
        if x >= len(days):
            return None
        return {"code": code, "day": days[e], "net": 0.05, "excess": 0.03, "peer": 0.02,
                "days": x - e + 1}  # fmt: skip

    return SimpleNamespace(
        p=panels, column={"S": 0}, opens=frame.to_numpy(), closes=(frame * 1.1).to_numpy(),
        turnover=pd.DataFrame(1e9, index=days, columns=["S"]), call=call,
    )  # fmt: skip


def _events(e: int) -> list[tuple[str, int, str]]:
    return [("S", e - 5, "fund_bulk"), ("S", e, "both_up")]


def test_a_new_call_is_recorded_with_what_made_it() -> None:
    (row,) = fw.new_events({}, _events(FIRST), _measure(FIRST + 1), NOW)
    assert row["event"] == "call"
    assert row["entry_on"] == "2026-11-09"
    assert row["entry"] == pytest.approx(100.0 * (1 + rr.SLIPPAGE))
    assert row["kinds"] == ["both_up", "fund_bulk"]


def test_a_call_entered_before_the_trial_does_not_count() -> None:
    assert fw.new_events({}, _events(FIRST - 1), _measure(FIRST + 1), NOW) == []


def test_evidence_from_before_the_trial_still_counts_as_evidence() -> None:
    (row,) = fw.new_events({}, [("S", FIRST - 30, "both_up"), ("S", FIRST, "fund_bulk")],
                           _measure(FIRST + 1), NOW)  # fmt: skip
    assert row["entry_on"] == "2026-11-09"


def test_a_session_with_no_price_waits_for_the_next_run() -> None:
    assert fw.new_events({}, _events(FIRST), _measure(FIRST + 1, float("nan")), NOW) == []


def test_a_recorded_call_is_not_recorded_twice() -> None:
    measure = _measure(FIRST + 1)
    first = fw.new_events({}, _events(FIRST), measure, NOW)
    assert fw.new_events(fw.fold(first), _events(FIRST), measure, NOW) == []


def test_a_held_call_closes_at_its_sixtieth_close_and_only_once() -> None:
    measure = _measure()
    (call, closed) = fw.new_events({}, _events(FIRST), measure, NOW)
    assert closed["event"] == "closed"
    assert closed["exit_on"] == str(DAYS[FIRST + 59])
    assert closed["exit"] == pytest.approx(110.0 * (1 - rr.SLIPPAGE))
    assert fw.new_events(fw.fold([call, closed]), _events(FIRST), measure, NOW) == []


def test_a_call_stays_open_until_its_sixtieth_session_exists() -> None:
    (call,) = fw.new_events({}, _events(FIRST), _measure(FIRST + 59), NOW)
    assert call["event"] == "call"


def _closed(n: int, excess: float = 0.03, net: float = 0.05) -> dict:
    start = date(2026, 11, 9)
    rows = {}
    for i in range(n):
        day = start + timedelta(days=7 * i)
        wobble = 0.01 if i % 2 else -0.01
        rows[str(i)] = {"entry_on": str(day), "exit_on": "x", "net": net, "excess": excess + wobble}
    return rows


def test_under_thirty_closed_is_only_collecting() -> None:
    got = fw.judge(_closed(29))
    assert got["status"] == "collecting"
    assert got["closed"] == 29


def test_open_calls_are_not_judged() -> None:
    got = fw.judge({"a": {"entry_on": "2026-11-09"}})
    assert (got["status"], got["open"], got["closed"]) == ("collecting", 1, 0)


def test_thirty_good_closes_pass() -> None:
    assert fw.judge(_closed(30))["status"] == "passed"


def test_no_money_after_charges_is_failing() -> None:
    assert fw.judge(_closed(30, net=-0.01))["status"] == "failing"


def test_no_edge_over_the_market_is_failing() -> None:
    assert fw.judge(_closed(30, excess=0.0))["status"] == "failing"


class FlatCost:
    def round_trip_cost(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(total=100.0)


def test_each_amount_pays_its_own_charges() -> None:
    calls = {"a": {"entry_on": "2026-11-09", "exit_on": "x", "entry": 100.0, "exit": 110.0}}
    table = fw.by_amount(calls, FlatCost())
    assert table["10000"]["mean_rupees"] == pytest.approx(1000 - 100)
    assert table["100000"]["mean_rupees"] == pytest.approx(10000 - 100)
