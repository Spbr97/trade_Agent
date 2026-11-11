"""hedged_straddle: the replay fills like the live straddle and the protection is counted fully."""

from __future__ import annotations

from typing import Any

import pytest
from tradedesk_lab import hedged_straddle as hs
from tradedesk_lab import option_income as oi

FREE = oi.OptionCosts(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
FLAT_FEE = oi.OptionCosts(0.0, 0.0, 0.0, 0.0, 0.0, 20.0)
Quotes = dict[int, tuple[float, float, float, float]]  # strike: call bid, ask, put bid, ask


def _minute(hhmm: str, ltp: float, quotes: Quotes, expiry: str = "2026-11-17") -> dict[str, Any]:
    strikes = {str(k): {"ce": [cb, ca, ca, 20.0, 0.5], "pe": [pb, pa, pa, 20.0, -0.5]}
               for k, (cb, ca, pb, pa) in quotes.items()}  # fmt: skip
    return {"t": f"2026-11-12T{hhmm}:03+05:30", "ltp": ltp, "expiry": expiry, "strikes": strikes}


def _book(atm: tuple[float, float, float, float], up: tuple[float, float],
          down: tuple[float, float]) -> Quotes:  # fmt: skip
    return {25000: atm, 25300: (*up, 0.0, 0.0), 24700: (0.0, 0.0, *down)}


OPEN = _book((100, 101, 100, 101), (10, 11), (10, 11))
QUIET_CLOSE = _book((60, 61, 50, 51), (4, 5), (4, 5))
CALL_RUNS = _book((134, 135, 70, 71), (30, 31), (6, 7))
WARM_UP = [_minute("09:17", 25001, OPEN), _minute("09:18", 25004, OPEN),
           _minute("09:19", 25002, OPEN)]  # fmt: skip


def _day(*later: dict[str, Any]) -> dict[str, Any]:
    got = hs.replay_day([*WARM_UP, _minute("09:20", 25003, OPEN), *later], FREE)
    assert got is not None
    return got


def _row(day: dict[str, Any], rule: str) -> dict[str, Any]:
    return next(r for r in day["hedged"] if r["rule"] == rule and r["wing"] == hs.WING)


def test_a_saved_minute_reads_like_a_live_chain() -> None:
    chain = hs.expand(_minute("09:20", 25003, OPEN))
    assert oi.atm_strike(chain) == 25000
    assert oi.quote(chain, 25300, "ce") == {"bid": 10, "ask": 11, "last": 11, "delta": 0.5}


def test_the_entry_sells_at_the_bid_and_buys_protection_at_the_ask() -> None:
    books = hs.open_hedged(hs.expand(_minute("09:20", 25003, OPEN)), "now", hs.WING)
    assert [b.name for b in books] == list(hs.VARIANTS)
    first = books[0]
    assert [(s.strike, s.entry) for s in first.shorts] == [(25000, 100), (25000, 100)]
    assert [(w.side, w.strike, w.entry) for w in first.wings] == [("ce", 25300, 11),
                                                                   ("pe", 24700, 11)]  # fmt: skip
    assert first.credit == 178
    assert first.shorts[0].stop == pytest.approx(130)
    assert hs.open_hedged(hs.expand(_minute("09:20", 25003, OPEN)), "now", 500) == []


def test_a_quiet_day_keeps_the_premium_less_the_protection() -> None:
    day = _day(_minute("12:00", 25010, OPEN), _minute("15:15", 25020, QUIET_CLOSE))
    hold = _row(day, "hedged_hold")
    assert hold["gross_inr"] == (100 - 61 + 100 - 51 + 4 - 11 + 4 - 11) * oi.LOT
    assert hold["cap_inr"] == (300 - 178) * oi.LOT
    assert hold["shorts"][0]["stop"] is None
    assert _row(day, "hedged_30")["net_inr"] == hold["net_inr"]
    assert day["quoted"] == {300: True, 200: False, 500: False}
    assert day["unfinished"] == []


def test_a_stop_closes_the_sold_option_and_its_protection_together() -> None:
    day = _day(_minute("10:00", 25200, CALL_RUNS), _minute("15:15", 25020, QUIET_CLOSE))
    thirty, forty, hold = (_row(day, n) for n in ("hedged_30", "hedged_40", "hedged_hold"))
    call, put = thirty["shorts"]
    assert (call["exit"], call["reason"]) == (135, "stop")
    assert (thirty["wings"][0]["exit"], thirty["wings"][0]["exit_at"]) == (30, call["exit_at"])
    assert put["reason"] == "15:15"
    assert thirty["gross_inr"] == (100 - 135 + 30 - 11 + 100 - 51 + 4 - 11) * oi.LOT
    assert forty["net_inr"] == hold["net_inr"]  # 135 is under the 40% stop of 140
    # the worst mark is the entry minute itself: four bid-ask spreads of one point each
    assert thirty["worst_open_inr"] == -4 * oi.LOT


def test_the_unhedged_straddles_are_replayed_from_the_same_minutes() -> None:
    day = _day(_minute("10:00", 25200, CALL_RUNS), _minute("15:15", 25020, QUIET_CLOSE))
    plain = {r["rule"]: r["gross_inr"] for r in day["plain"]}
    assert plain == {"straddle_30": (100 - 135 + 100 - 51) * oi.LOT,
                     "straddle_40": (100 - 61 + 100 - 51) * oi.LOT}  # fmt: skip


def test_protection_with_no_bid_expires_without_a_sell_order() -> None:
    worthless = _book((60, 61, 50, 51), (0, 0.05), (4, 5))
    start = [*WARM_UP, _minute("09:20", 25003, OPEN)]
    with_bid = hs.replay_day([*start, _minute("15:15", 25020, QUIET_CLOSE)], FLAT_FEE)
    no_bid = hs.replay_day([*start, _minute("15:15", 25020, worthless)], FLAT_FEE)
    assert with_bid is not None and no_bid is not None
    assert _row(with_bid, "hedged_hold")["costs_inr"] == 8 * 20
    row = _row(no_bid, "hedged_hold")
    assert row["costs_inr"] == 7 * 20
    assert row["wings"][0]["exit"] == 0


def test_a_holiday_or_a_day_cut_short_is_not_a_result() -> None:
    still = [_minute(t, 25000, OPEN) for t in ("09:17", "09:18", "09:19", "09:20", "15:15")]
    assert hs.replay_day(still, FREE) is None
    assert hs.replay_day(WARM_UP, FREE) is None
    cut = _day(_minute("12:00", 25010, OPEN))
    assert cut["hedged"] == []
    assert "hedged_hold/300" in cut["unfinished"]


def test_another_expirys_minutes_are_ignored() -> None:
    far = _minute("15:15", 25020, CALL_RUNS, expiry="2026-11-24")
    day = _day(far, _minute("15:15", 25020, QUIET_CLOSE))
    assert _row(day, "hedged_hold")["shorts"][0]["exit"] == 61


def _hedged_day(day: str, nets: dict[str, float], cap: float = 8000.0) -> dict[str, Any]:
    rows = [{"rule": n, "wing": hs.WING, "net_inr": v, "credit_points": 170.0, "cap_inr": cap,
             "costs_inr": 200.0, "worst_open_inr": -500.0, "opened_at": f"{day}T09:20"}
            for n, v in nets.items()]  # fmt: skip
    plain = [{"rule": "straddle_30", "net_inr": 1000.0}]
    return {"day": day, "hedged": rows, "plain": plain, "quoted": {300: True, 200: 0, 500: 0}}


def test_the_summary_keeps_profit_and_loss_apart_and_checks_the_ledger() -> None:
    good = {"hedged_30": 900.0, "hedged_40": 800.0, "hedged_hold": 700.0}
    bad = {"hedged_30": -400.0, "hedged_40": -300.0, "hedged_hold": -200.0}
    days = [_hedged_day("2026-11-02", good), _hedged_day("2026-11-03", bad)]
    ledger = [{"rule": "straddle_30", "closed_on": "2026-11-02", "net_inr": 1000.0},
              {"rule": "straddle_30", "closed_on": "2026-11-03", "net_inr": 940.0},
              {"rule": "condor", "closed_on": "2026-11-03", "net_inr": -5.0}]  # fmt: skip
    got = hs.summarise(days, ledger)
    book = got["books"]["hedged_30"]
    assert (book["net_inr"], book["total_profit"], book["total_loss"]) == (500.0, 900.0, -400.0)
    assert (book["sessions"], book["won"], book["worst_session"]) == (2, 1, -400.0)
    assert got["ledger_check"] == {"sessions_compared": 2, "largest_gap_inr": 60.0}
    assert got["worth_a_forward_trial"] and got["fails"] == []


def test_each_reason_not_to_run_a_trial_is_named() -> None:
    lose = {"hedged_30": -400.0, "hedged_40": -300.0, "hedged_hold": -9000.0}
    day = _hedged_day("2026-11-03", lose)
    missing = {**_hedged_day("2026-11-04", {}), "quoted": {300: False, 200: 0, 500: 0}}
    got = hs.summarise([day, missing], [])
    assert got["fails"] == ["quoted on only 1 of 2 sessions", "no version's profit beats its loss",
                            "hedged_hold lost more than its cap on ['2026-11-03']"]  # fmt: skip
