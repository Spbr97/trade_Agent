"""convergence: a call needs distinct kinds of evidence, all known by its entry."""

from __future__ import annotations

from tradedesk_lab import convergence as cv


def test_two_kinds_close_together_make_a_call_at_the_second() -> None:
    events = [("S", 10, "fund_bulk"), ("S", 15, "both_up")]
    assert cv.convergent(events, 2, 20) == [("S", 15)]


def test_the_same_kind_twice_is_not_agreement() -> None:
    events = [("S", 10, "fund_bulk"), ("S", 15, "fund_bulk")]
    assert cv.convergent(events, 2, 20) == []


def test_evidence_outside_the_window_does_not_count() -> None:
    events = [("S", 10, "fund_bulk"), ("S", 40, "both_up")]
    assert cv.convergent(events, 2, 20) == []
    assert cv.convergent(events, 2, 60) == [("S", 40)]


def test_the_window_edge_is_exact() -> None:
    assert cv.convergent([("S", 10, "a"), ("S", 29, "b")], 2, 20) == [("S", 29)]
    assert cv.convergent([("S", 10, "a"), ("S", 30, "b")], 2, 20) == []


def test_a_later_event_cannot_make_an_earlier_call() -> None:
    (call,) = cv.convergent([("S", 15, "both_up"), ("S", 10, "fund_bulk")], 2, 20)
    assert call == ("S", 15)  # never at 10, when the second kind did not yet exist


def test_two_events_at_the_same_entry_count_at_that_entry() -> None:
    assert cv.convergent([("S", 10, "a"), ("S", 10, "b")], 2, 20) == [("S", 10)]


def test_other_stocks_do_not_help() -> None:
    assert cv.convergent([("S", 10, "a"), ("T", 11, "b")], 2, 20) == []


def test_all_three_needs_all_three() -> None:
    assert cv.convergent([("S", 10, "a"), ("S", 20, "b")], 3, 60) == []
    assert cv.convergent([("S", 10, "a"), ("S", 20, "b"), ("S", 30, "c")], 3, 60) == [("S", 30)]


def test_one_call_per_stock_per_hold() -> None:
    events = [("S", 10, "a"), ("S", 12, "b"), ("S", 20, "a"), ("S", 75, "b")]
    assert cv.convergent(events, 2, 70) == [("S", 12), ("S", 75)]


def test_the_alone_baseline_takes_each_kind_once_per_hold() -> None:
    assert cv.convergent([("S", 10, "a"), ("S", 30, "a"), ("S", 80, "a")], 1, 1) == [
        ("S", 10), ("S", 80),
    ]  # fmt: skip
