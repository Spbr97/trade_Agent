"""hedged_straddle's forward trial: each finished session is recorded once and judged by its bar."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk_lab import hedged_straddle as hs
from tradedesk_lab import option_income as oi

from tradedesk.broker.indstocks.models import IST

FREE = oi.OptionCosts(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
OPEN = {25000: (100, 101, 100, 101), 25300: (10, 11, 0, 0), 24700: (0, 0, 10, 11)}
CLOSE = {25000: (60, 61, 50, 51), 25300: (4, 5, 0, 0), 24700: (0, 0, 4, 5)}


def _minute(day: str, hhmm: str, ltp: float, quotes: dict[int, tuple]) -> dict[str, Any]:
    strikes = {str(k): {"ce": [cb, ca, ca, 20.0, 0.5], "pe": [pb, pa, pa, 20.0, -0.5]}
               for k, (cb, ca, pb, pa) in quotes.items()}  # fmt: skip
    return {"t": f"{day}T{hhmm}:03+05:30", "ltp": ltp, "expiry": "2026-11-17", "strikes": strikes}


def _save(folder: Path, day: str, *, finished: bool = True) -> None:
    rows = [_minute(day, t, 25000 + i, OPEN) for i, t in enumerate(("09:17", "09:18", "09:20"))]
    if finished:
        rows.append(_minute(day, "15:15", 25020, CLOSE))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows), "utf-8")


def _run(tmp_path: Path, when: str) -> dict[str, Any]:
    return hs.forward(
        datetime.fromisoformat(when).replace(tzinfo=IST), chains=tmp_path / "chain",
        ledger=tmp_path / "ledger.jsonl", verdict=tmp_path / "forward.json", costs=FREE,
    )  # fmt: skip


def test_a_finished_session_is_recorded_once(tmp_path: Path) -> None:
    _save(tmp_path / "chain", "2026-11-10")  # before the trial started
    _save(tmp_path / "chain", "2026-11-12")
    first = _run(tmp_path, "2026-11-12T15:45")
    assert first["new_rows"] == 3
    assert _run(tmp_path, "2026-11-12T15:50")["new_rows"] == 0
    rows = hs.read_ledger(tmp_path / "ledger.jsonl")
    assert {r["rule"] for r in rows} == set(hs.VARIANTS)
    assert {r["closed_on"] for r in rows} == {"2026-11-12"}
    assert first["books"]["hedged_40"]["sessions"] == 1
    assert first["books"]["hedged_40"]["fails"] == ["1 of 60 sessions"]


def test_todays_session_waits_and_a_broken_old_one_is_noted(tmp_path: Path) -> None:
    _save(tmp_path / "chain", "2026-11-12", finished=False)
    assert _run(tmp_path, "2026-11-12T11:00")["new_rows"] == 0
    later = _run(tmp_path, "2026-11-13T15:45")
    assert later["unfinished_sessions"] == ["2026-11-12"]
    assert _run(tmp_path, "2026-11-13T15:50")["new_rows"] == 0


def _row(rule: str, net: float, day: int) -> dict[str, Any]:
    return {"rule": rule, "net_inr": net, "cap_inr": 7000.0, "costs_inr": 230.0,
            "closed_on": f"day{day}"}  # fmt: skip


def test_the_forward_bar_names_each_failing_part() -> None:
    good = [_row("hedged_40", 900.0 if i % 4 else -500.0, i) for i in range(60)]
    assert hs.judge_forward(good)["hedged_40"]["passed_bars_1_to_3"]
    thin = [_row("hedged_30", 600.0 if i % 2 else -500.0, i) for i in range(60)]
    assert hs.judge_forward(thin)["hedged_30"]["fails"] == ["profit under 1.5 times loss"]
    blown = [*good[:-1], _row("hedged_40", -7500.0, 99)]
    assert "beyond its cap on ['day99']" in hs.judge_forward(blown)["hedged_40"]["fails"]
    fading = [*[_row("hedged_hold", 5000.0, i) for i in range(40)],
              *[_row("hedged_hold", -100.0, 40 + i) for i in range(20)]]  # fmt: skip
    assert hs.judge_forward(fading)["hedged_hold"]["fails"] == ["last 20 sessions not in profit"]
    empty = hs.judge_forward([])["hedged_30"]["fails"]
    assert empty[:2] == ["0 of 60 sessions", "profit under 1.5 times loss"]
