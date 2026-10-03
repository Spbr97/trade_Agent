from datetime import date

import pandas as pd
from tradedesk_lab.accuracy_geometry import EntrySpec, PriceBar
from tradedesk_lab.accuracy_geometry import _quick_outcome as m7_outcome
from tradedesk_lab.accuracy_prospective_shadow import (
    _quick_outcome as m8_outcome,
)
from tradedesk_lab.accuracy_prospective_shadow import (
    summarize,
)
from tradedesk_lab.artifacts import ROOT

from tradedesk.config import load_config
from tradedesk.markets.market import nse_market


def test_m8_outcome_matches_frozen_m7_geometry() -> None:
    days = pd.date_range("2026-01-05", periods=4, freq="B")
    frame = pd.DataFrame(
        {
            "open": [100.0, 100.2, 100.3, 100.4],
            "high": [101.2, 100.8, 100.9, 101.0],
            "low": [99.5, 99.8, 99.9, 100.0],
            "close": [100.8, 100.5, 100.6, 100.7],
        },
        index=days,
    )
    entry = EntrySpec(
        on=days[0].date(),
        fill=100.0,
        at_open=True,
        future=tuple(
            PriceBar(
                on=stamp.date(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
            )
            for stamp, row in frame.iterrows()
        ),
    )
    costs = nse_market(load_config(ROOT)).costs
    expected = m7_outcome(
        entry,
        stop=98.0,
        target=101.0,
        max_hold=3,
        qty=100.0,
        costs=costs,
    )
    actual = m8_outcome(
        frame,
        fill=100.0,
        stop=98.0,
        target=101.0,
        max_hold=3,
        qty=100.0,
        costs=costs,
    )
    assert actual is not None
    assert expected is not None
    assert actual[0] == expected[0]
    assert actual[1] == expected[1]


def _record(day: date, *, label: int, net_r: float) -> dict:
    return {
        "armed_on": day.isoformat(),
        "selected": True,
        "prospective_eligible": True,
        "status": "resolved",
        "label": label,
        "net_r": net_r,
    }


def test_summary_keeps_small_samples_insufficient() -> None:
    state = {"records": [_record(date(2026, 1, 5), label=1, net_r=0.4)]}
    summary = summarize(state)
    assert summary["status"] == "collecting_insufficient_evidence"
    assert summary["accuracy"] == 1.0
    assert summary["eligible_for_live"] is False
    assert not summary["gate_checks"]["resolved_calls"]


def test_summary_requires_every_prospective_gate() -> None:
    records = []
    for offset in range(50):
        day = date.fromordinal(date(2026, 1, 1).toordinal() + offset)
        records.extend([_record(day, label=1, net_r=0.3), _record(day, label=1, net_r=0.2)])
    summary = summarize({"records": records})
    assert summary["status"] == "prospective_pass"
    assert summary["resolved_calls"] == 100
    assert summary["active_sessions"] == 50
    assert all(summary["gate_checks"].values())
    assert summary["eligible_for_live"] is False
