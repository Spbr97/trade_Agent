"""pick_announcer.py: each consensus row is announced once, research-only."""

from __future__ import annotations

import json
from datetime import date

from tradedesk.pick_announcer import announce_new_picks


def test_consensus_rows_are_announced_once_with_symbols(tmp_path) -> None:
    chain = tmp_path / "nse" / "forward_picks.jsonl"
    chain.parent.mkdir(parents=True)
    rows = [
        {"event": "picks_registered", "candidate": "consensus_top3:opening_bar",
         "checkpoint": "opening_bar", "decision_session": "2026-10-16", "entry_bar": "09:45",
         "codes": ["NSE_1594", "NSE_999"]},
        {"event": "picks_registered", "candidate": "opening_bar:gap_top10",
         "checkpoint": "opening_bar", "decision_session": "2026-10-16", "entry_bar": "09:45",
         "codes": ["NSE_1594"]},
        {"event": "picks_registered", "candidate": "consensus_top3:first_hour",
         "checkpoint": "first_hour", "decision_session": "2026-10-15", "entry_bar": "10:30",
         "codes": ["NSE_1594"]},
    ]  # fmt: skip
    chain.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    instruments = tmp_path / "equity.csv"
    instruments.write_text("NSE,E,1594,EQUITY,,INFY,1,INFOSYS\n", encoding="utf-8")
    sent: list[tuple[str, str]] = []
    kwargs = dict(
        lab_root=tmp_path, state_path=tmp_path / "state.json", instruments=instruments,
        send=lambda title, body: sent.append((title, body)),
    )  # fmt: skip
    first = announce_new_picks("nse", date(2026, 10, 16), **kwargs)
    second = announce_new_picks("nse", date(2026, 10, 16), **kwargs)
    assert len(first) == 1 and second == []
    assert "RESEARCH-ONLY" in sent[0][0] and "not an alert" in sent[0][0]
    assert sent[0][1] == "SHORT at 09:45 open (opening bar): INFY, NSE_999"
