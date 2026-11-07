"""money_board: the owner's number must count what a real Rs 1 lakh book could have held."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from tradedesk import earnings_drift as ed
from tradedesk import money_board as mb

NOW = datetime(2026, 11, 7, 21, 10)


def _call(i: int, entry_on: str = "2026-10-20", **extra: Any) -> dict[str, Any]:
    return {"call_id": f"S{i}", "symbol": f"S{i}", "scrip_code": f"NSE_{i}", "entry_on": entry_on,
            "entry_price": 100.0, "qty": 100, **extra}  # fmt: skip


def _closed(i: int, entry_on: str, exit_on: str, net_inr: float) -> dict[str, Any]:
    return _call(i, entry_on, exit_on=exit_on, exit_price=110.0, net_inr=net_inr)


def test_the_book_never_holds_more_than_ten_slots() -> None:
    calls = [_call(i) for i in range(12)]
    got = mb.drift_book(calls, {f"NSE_{i}": 100.0 for i in range(12)})
    assert got["taken"] == 10
    assert got["skipped_full"] == 2


def test_a_slot_frees_only_after_its_exit_day() -> None:
    held = [_closed(0, "2026-10-20", "2026-10-22", 500.0)]
    held += [_call(i) for i in range(1, 10)]
    same_day = mb.drift_book([*held, _call(10, "2026-10-22")], {})
    next_day = mb.drift_book([*held, _call(10, "2026-10-23")], {})
    assert same_day["skipped_full"] == 1
    assert next_day["skipped_full"] == 0


def test_marked_and_unfillable_calls_are_not_taken() -> None:
    calls = [
        _call(0, down_day=True, nifty_vs_200dma="below"),
        _call(1, unfillable_at="2026-10-20"),
        {"call_id": "waiting", "scrip_code": "NSE_2"},
    ]
    got = mb.drift_book(calls, {"NSE_0": 200.0, "NSE_1": 200.0})
    assert got["taken"] == 0
    assert got["skipped_marked"] == 1
    assert got["open_inr"] == 0


def test_open_trades_are_marked_after_their_round_trip_charges() -> None:
    got = mb.drift_book([_call(0)], {"NSE_0": 110.0})
    assert got["open_inr"] == pytest.approx((0.10 - ed.HISTORY["round_trip"]) * 10_000)
    assert got["closed"] == 0


def test_an_open_trade_without_a_price_is_reported_not_guessed() -> None:
    got = mb.drift_book([_call(0)], {"NSE_0": float("nan")})
    assert got["unpriced"] == 1
    assert got["open_inr"] == 0


def _nifty(first: float, last: float) -> pd.Series:
    days = [date(2026, 10, 19) + timedelta(days=i) for i in range(5)]
    return pd.Series([first, first, first, first, last], index=days)


def test_the_nifty_runs_from_the_close_before_the_start() -> None:
    days = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    nifty = pd.Series([100.0, 200.0, 110.0], index=days)
    assert mb.nifty_move(nifty, date(2026, 10, 20), date(2026, 10, 21)) == pytest.approx(0.10)
    assert mb.nifty_move(nifty, date(2026, 10, 19), date(2026, 10, 21)) is None
    assert mb.nifty_move(None, date(2026, 10, 20), date(2026, 10, 21)) is None


def _option(rule: str, day: str, net: float) -> dict[str, Any]:
    return {"rule": rule, "opened_at": f"{day}T09:20:00+05:30", "closed_on": day, "net_inr": net}


def _build(ledger: list[dict[str, Any]], options: list[dict[str, Any]]) -> dict[str, Any]:
    return mb.build(ledger, options, {}, _nifty(100.0, 101.0), date(2026, 10, 23), NOW)


def test_a_straddle_is_scaled_to_a_lakh_through_its_margin() -> None:
    rows = [_option("straddle_30", "2026-10-20", 7200.0), _option("straddle_30", "2026-10-23", -3600.0)]  # noqa: E501
    (_, straddle, *_) = _build([], rows)["rows"]
    assert straddle["rupees"] == 3600.0
    assert straddle["per_lakh"] == pytest.approx(1000.0)
    assert straddle["nifty_per_lakh"] == pytest.approx(1000.0)
    assert straddle["worst_inr"] == -3600.0
    assert (straddle["closed"], straddle["won"]) == (2, 1)


def test_the_losing_condor_stays_on_the_board_without_a_lakh_figure() -> None:
    (*_, condor) = _build([], [_option("condor", "2026-10-22", -9852.84)])["rows"]
    assert condor["rupees"] == -9852.84
    assert condor["per_lakh"] is None
    assert condor["ahead"] is None


def test_nothing_is_called_ahead_before_enough_trades_close() -> None:
    rows = [_option("straddle_40", "2026-10-20", 50_000.0)]
    assert _build([], rows)["rows"][2]["verdict"] == "Too early: 1 of 60 closed"
    many = [_option("straddle_40", "2026-10-20", 50_000.0)] * mb.STRADDLE_SESSIONS_NEEDED
    assert _build([], many)["rows"][2]["verdict"] == "60 closed: ahead of the Nifty"


def test_drift_is_read_from_the_real_ledger_events() -> None:
    ledger = [
        {"event": "call", "call_id": "A:1", "at": "x", "symbol": "A", "scrip_code": "NSE_1"},
        {"event": "entered", "call_id": "A:1", "at": "x", "entry_on": "2026-10-20",
         "entry_price": 100.0, "qty": 100},
        {"event": "exited", "call_id": "A:1", "at": "x", "exit_on": "2026-10-22",
         "exit_price": 110.0, "net_return": 0.094, "excess_return": 0.05, "net_inr": 940.0},
    ]  # fmt: skip
    (drift, *_) = _build(ledger, [])["rows"]
    assert (drift["closed"], drift["won"], drift["open"]) == (1, 1, 0)
    assert drift["rupees"] == 940.0
    assert drift["nifty_per_lakh"] == pytest.approx(1000.0)
    assert drift["ahead"] is False
    assert drift["verdict"] == "Too early: 1 of 92 paper calls closed"


def test_drifts_progress_counts_paper_calls_the_slots_could_not_hold() -> None:
    ledger: list[dict[str, Any]] = []
    for i in range(12):
        cid = f"S{i}:1"
        ledger += [
            {"event": "call", "call_id": cid, "at": "x", "symbol": f"S{i}", "scrip_code": f"N{i}"},
            {"event": "entered", "call_id": cid, "at": "x", "entry_on": "2026-10-20",
             "entry_price": 100.0, "qty": 100},
            {"event": "exited", "call_id": cid, "at": "x", "exit_on": "2026-10-22",
             "exit_price": 110.0, "net_return": 0.094, "excess_return": 0.05, "net_inr": 940.0},
        ]  # fmt: skip
    (drift, *_) = _build(ledger, [])["rows"]
    assert drift["closed"] == 10
    assert drift["verdict"] == "Too early: 12 of 92 paper calls closed"
