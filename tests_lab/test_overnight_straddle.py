"""overnight_straddle: a night is sold at the close, bought back next morning and never lost."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk_lab import option_income as oi
from tradedesk_lab import overnight_straddle as on

from tradedesk.broker.indstocks.models import IST

FREE = oi.OptionCosts(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
# strike -> (call bid, call ask, put bid, put ask)
EVENING = {25000: (100, 101, 100, 101), 25300: (10, 11, 0, 0), 24700: (0, 0, 10, 11)}
QUIET = {25000: (80, 81, 80, 81), 25300: (6, 7, 0, 0), 24700: (0, 0, 6, 7)}
GAP_DOWN = {25000: (2, 3, 395, 400), 25300: (0, 1, 0, 0), 24700: (0, 0, 120, 125)}
FAR_AWAY = {23000: (50, 51, 50, 51)}


def _minute(day: str, hhmm: str, ltp: float, quotes: dict[int, tuple], expiry: str) -> (
    dict[str, Any]
):  # fmt: skip
    strikes = {str(k): {"ce": [cb, ca, ca, 20.0, 0.5], "pe": [pb, pa, pa, 20.0, -0.5]}
               for k, (cb, ca, pb, pa) in quotes.items()}  # fmt: skip
    return {"t": f"{day}T{hhmm}:03+05:30", "ltp": ltp, "expiry": expiry, "strikes": strikes}


def _save(folder: Path, day: str, morning: dict[int, tuple], *, ltp: float = 25000.0,
          expiry: str = "2026-11-24", close: bool = True) -> None:  # fmt: skip
    rows = [_minute(day, t, ltp + i, morning, expiry) for i, t in enumerate(("09:17", "09:18"))]
    rows.append(_minute(day, "09:20", ltp + 2, morning, expiry))
    if close:
        rows.append(_minute(day, "15:15", 25010.0, EVENING, expiry))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows), "utf-8")


def _books(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["rule"]: r for r in rows}


def test_a_quiet_night_keeps_the_decay_of_all_four_options(tmp_path: Path) -> None:
    _save(tmp_path, "2026-11-18", QUIET)
    _save(tmp_path, "2026-11-19", QUIET)
    books = _books(on.replay(tmp_path, FREE))
    hedged, plain = books["night_hedged"], books["night_plain"]
    # sold 100 + 100, bought back at 81 + 81; protection bought at 11 + 11, sold at 6 + 6
    assert plain["net_inr"] == (200 - 162) * oi.LOT
    assert hedged["net_inr"] == (200 - 162 - 22 + 12) * oi.LOT
    assert hedged["cap_inr"] == (300 - 178) * oi.LOT and hedged["nights"] == 1
    assert hedged["closed_at"][11:16] == "09:20"


def test_a_gap_is_cushioned_by_the_protection(tmp_path: Path) -> None:
    _save(tmp_path, "2026-11-18", QUIET)
    _save(tmp_path, "2026-11-19", GAP_DOWN, ltp=24600.0)
    books = _books(on.replay(tmp_path, FREE))
    hedged = books["night_hedged"]
    assert books["night_plain"]["net_inr"] == (200 - 3 - 400) * oi.LOT
    assert hedged["net_inr"] == (200 - 3 - 400 - 22 + 0 + 120) * oi.LOT
    assert -hedged["net_inr"] <= hedged["cap_inr"]
    assert on.judge(list(books.values()), on.MIN_NIGHTS)["fails"][1:] == [
        "profit does not beat loss"
    ]


def test_a_gap_beyond_the_saved_strikes_is_booked_at_the_cap(tmp_path: Path) -> None:
    _save(tmp_path, "2026-11-18", QUIET)
    _save(tmp_path, "2026-11-19", FAR_AWAY, ltp=23000.0)
    books = _books(on.replay(tmp_path, FREE))
    assert books["night_hedged"]["net_inr"] == -books["night_hedged"]["cap_inr"]
    assert books["night_hedged"]["priced"] is False and books["night_plain"]["priced"] is False
    verdict = on.judge(list(books.values()), on.MIN_NIGHTS)
    assert verdict["books"]["night_plain"]["unpriced"] == 1
    assert verdict["books"]["night_hedged"]["sessions"] == 1


def test_nothing_is_carried_from_an_expiry_day_or_into_a_holiday(tmp_path: Path) -> None:
    _save(tmp_path, "2026-11-24", QUIET)  # the nearest contract expires this evening
    _save(tmp_path, "2026-11-25", QUIET, expiry="2026-12-01")
    assert on.replay(tmp_path, FREE) == []
    flat = [_minute("2026-11-27", t, 25000.0, QUIET, "2026-12-01")
            for t in ("09:17", "09:18", "09:20")]  # fmt: skip
    (tmp_path / "2026-11-27.jsonl").write_text("\n".join(json.dumps(r) for r in flat), "utf-8")
    assert [r["opened_at"][:10] for r in on.replay(tmp_path, FREE)] == []


def test_the_forward_trial_records_each_night_once(tmp_path: Path) -> None:
    chains = tmp_path / "chain"
    _save(chains, "2026-11-12", QUIET)  # before the trial started
    _save(chains, "2026-11-13", QUIET)
    _save(chains, "2026-11-16", QUIET, close=False)  # the next session, still in progress

    def run(when: str) -> dict[str, Any]:
        return on.forward(
            datetime.fromisoformat(when).replace(tzinfo=IST), chains=chains,
            ledger=tmp_path / "ledger.jsonl", verdict=tmp_path / "forward.json", costs=FREE,
        )  # fmt: skip

    first = run("2026-11-16T09:30")
    assert first["new_rows"] == 2 and run("2026-11-16T09:35")["new_rows"] == 0
    rows = on.read_ledger(tmp_path / "ledger.jsonl")
    assert {r["opened_at"][:10] for r in rows} == {"2026-11-13"}
    assert rows[0]["nights"] == 3
    assert first["books"]["night_hedged"]["sessions"] == 1
    assert first["fails"][0] == "1 of 60 nights" and not first["passed_bars_1_to_3"]


def test_the_bars_name_what_fails() -> None:
    def row(net: float, day: int) -> dict[str, Any]:
        return {"rule": "night_hedged", "net_inr": net, "cap_inr": 7000.0, "costs_inr": 250.0,
                "credit_points": 190.0, "priced": True, "closed_on": f"day{day}"}  # fmt: skip

    good = [row(900.0 if i % 4 else -500.0, i) for i in range(60)]
    assert on.judge(good, on.NIGHTS_NEEDED)["fails"] == []
    thin = [row(600.0 if i % 2 else -500.0, i) for i in range(60)]
    assert on.judge(thin, on.NIGHTS_NEEDED)["fails"] == ["profit under 1.5 times loss"]
    blown = [*good[:-1], row(-7500.0, 99)]
    assert "beyond its cap on ['day99']" in on.judge(blown, on.NIGHTS_NEEDED)["fails"]
    few = on.judge([row(500.0, 1), row(-600.0, 2)], on.MIN_NIGHTS)["fails"]
    assert few == ["2 of 8 nights", "profit does not beat loss"]
