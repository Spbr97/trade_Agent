"""money_board: the protected straddles appear per lot, with no Rs 1 lakh figure yet."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from tradedesk import money_board as mb

NOW = datetime(2026, 11, 12, 21, 10)


def _hedged(rule: str, day: str, net: float) -> dict[str, Any]:
    return {"rule": rule, "opened_at": f"{day}T09:20:03+05:30", "closed_on": day, "net_inr": net}


def _build(hedged: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    days = [date(2026, 11, 9) + timedelta(days=i) for i in range(5)]
    nifty = pd.Series([100.0, 100.0, 100.0, 101.0, 101.0], index=days)
    board = mb.build([], [], {}, nifty, date(2026, 11, 13), NOW, hedged)
    return {row["name"]: row for row in board["rows"]}


def test_protected_straddles_sit_between_the_straddles_and_the_condor() -> None:
    rows = _build([_hedged("hedged_30", "2026-11-11", 900.0),
                   _hedged("hedged_30", "2026-11-12", -400.0)])  # fmt: skip
    assert list(rows)[3:] == [*mb.HEDGED.values(), "Friday condor"]
    row = rows["Protected straddle, 30% stop"]
    assert (row["closed"], row["won"], row["rupees"]) == (2, 1, 500.0)
    assert (row["profit_inr"], row["loss_inr"]) == (900.0, -400.0)
    assert row["per_lakh"] is None and row["ahead"] is None
    assert row["verdict"] == "Too early: 2 of 60 closed"
    assert rows["Protected straddle, no stop"]["closed"] == 0


def test_a_protected_straddle_is_paused_after_twenty_losing_sessions() -> None:
    losing = [_hedged("hedged_40", "2026-11-11", -50.0)] * mb.STRADDLE_PAUSE_SESSIONS
    assert _build(losing)["Protected straddle, 40% stop"]["verdict"].startswith("PAUSE")
