"""money_board: the drift book with its free slots in the Nifty (tradedesk_lab/drift_core.py)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from tradedesk import money_board as mb

DAYS = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]


def test_free_slots_earn_the_nifty_and_each_call_pays_one_switch() -> None:
    nifty = pd.Series([100.0, 110.0, 110.0, 99.0], index=DAYS)
    spans = [("2026-10-20", "2026-10-21"), ("2026-10-21", None)]
    index, charges = mb.core_index_inr(spans, nifty, date(2026, 10, 22))
    # 10-20 -> 10-21: flat. 10-21 -> 10-22: -10% on the 9 slots free on 10-22.
    assert index == pytest.approx(9 * 10_000 * -0.10)
    assert charges == pytest.approx(2 * 35.0)
    assert mb.core_index_inr([], nifty, date(2026, 10, 22)) is None


def test_the_core_row_follows_the_drift_row() -> None:
    nifty = pd.Series([100.0, 100.0, 102.0, 102.0], index=DAYS)
    call = {"call_id": "S0", "symbol": "S0", "scrip_code": "NSE_0", "entry_on": "2026-10-20",
            "entry_price": 100.0, "qty": 100}  # fmt: skip
    board = mb.build([], [], {"NSE_0": 100.0}, nifty, date(2026, 10, 22),
                     datetime(2026, 10, 22, 21, 10))  # fmt: skip
    assert [r["name"] for r in board["rows"]][:2] == ["Drift", "Straddle, 30% stop"]
    ledger = [{"event": "call", "at": "2026-10-19T16:25", **call},
              {"event": "entered", "at": "2026-10-20T16:25", "call_id": "S0",
               "entry_on": "2026-10-20", "entry_price": 100.0, "qty": 100}]  # fmt: skip
    board = mb.build(ledger, [], {"NSE_0": 100.0}, nifty, date(2026, 10, 22),
                     datetime(2026, 10, 22, 21, 10))  # fmt: skip
    rows = {r["name"]: r for r in board["rows"]}
    assert list(rows)[:2] == ["Drift", "Drift + Nifty in free slots"]
    core, drift = rows["Drift + Nifty in free slots"], rows["Drift"]
    assert core["rupees"] == pytest.approx(drift["rupees"] + 9 * 10_000 * 0.02 - 35.0)
    assert core["nifty_per_lakh"] == pytest.approx(drift["nifty_per_lakh"])
    assert core["verdict"] == drift["verdict"]
