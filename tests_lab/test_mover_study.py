"""mover_study.py: hindsight can generate ideas but never evidence. Observations decompose a
move into its uncatchable gap and catchable remainder; the search uses only previous-close
features against tradeable next-session targets and a same-day stock null; forward picks are
registered before the next open or not at all."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import mover_study as ms

from tradedesk.broker.indstocks.models import IST
from tradedesk.filings import Filing


def _panel(n_days: int = 200, n_stocks: int = 40, *, planted: bool, seed: int = 1) -> pd.DataFrame:
    """Synthetic panel. When `planted`, stocks in the top decile of `vol_rel` at the close
    earn +1% next open->close; otherwise every target is pure noise."""

    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-01-01", periods=n_days).date
    rows = []
    for d in days:
        for s in range(n_stocks):
            feats = {f: rng.normal() for f in ms.FEATURES}
            rows.append({"date": d, "code": f"NSE_{s}", "res_next": 0.0, **feats})
    frame = pd.DataFrame(rows)
    noise = rng.normal(0, 0.01, len(frame))
    frame["open_to_close_1d"] = noise
    frame["open_to_close_5d"] = rng.normal(0, 0.02, len(frame))
    if planted:
        top = frame.groupby("date")["vol_rel"].rank(pct=True) >= 0.9
        frame.loc[top, "open_to_close_1d"] += 0.01
    return frame


COSTS = {"open_to_close_1d": 0.001, "open_to_close_5d": 0.003}


def _grammar():
    return [(ms.Clause(f, side, 0.10),) for f in ms.FEATURES for side in ("top", "bottom")]


@pytest.fixture(autouse=True)
def _small_sample_floors(monkeypatch):
    monkeypatch.setattr(ms, "MIN_TRAIN_PICKS", 100)
    monkeypatch.setattr(ms, "MIN_TRAIN_DAYS", 50)
    monkeypatch.setattr(ms, "MIN_VALIDATION_DAYS", 20)


def test_search_finds_a_planted_edge_and_only_that() -> None:
    result = ms.search(_panel(planted=True), COSTS, grammar=_grammar())
    names = {(s["name"], s["target"]) for s in result["survivors"]}
    assert names == {("vol_rel_top10", "open_to_close_1d")}


def test_search_finds_nothing_in_noise() -> None:
    result = ms.search(_panel(planted=False, seed=7), COSTS, grammar=_grammar())
    assert result["survivors"] == []


def test_costs_can_kill_a_real_gross_edge() -> None:
    result = ms.search(
        _panel(planted=True), {"open_to_close_1d": 0.02, "open_to_close_5d": 0.02},
        grammar=_grammar(),
    )  # fmt: skip
    assert result["survivors"] == []


def test_full_grammar_is_fixed_and_unique() -> None:
    grammar = ms.conditions()
    names = [ms.condition_name(c) for c in grammar]
    assert len(names) == len(set(names)) == 313


def _obs_panel() -> pd.DataFrame:
    d = date(2026, 10, 14)
    return pd.DataFrame(
        [
            # TARIL: +15% day, +9.5% of it already in the gap
            {"date": d, "code": "NSE_15174", "prev_close": 304.0, "open_today": 333.0,
             "high_today": 360.8, "close": 350.5, "res_today": 0.0},
            # ELECON: flat open, the whole move after a 13:19 results filing
            {"date": d, "code": "NSE_13643", "prev_close": 440.0, "open_today": 440.0,
             "high_today": 501.8, "close": 482.55, "res_today": 1.0},
            {"date": d, "code": "NSE_1", "prev_close": 100.0, "open_today": 100.0,
             "high_today": 101.0, "close": 100.5, "res_today": 0.0},
        ]
    )  # fmt: skip


def test_observation_splits_gap_from_catchable_and_times_filings() -> None:
    names = {"NSE_15174": "TARIL", "NSE_13643": "ELECON", "NSE_1": "FLAT"}
    filings = [
        Filing("TARIL", "2026-10-13T15:37:10+05:30", "results", "Outcome", "", "1"),
        Filing("ELECON", "2026-10-14T13:19:48+05:30", "results", "Outcome", "", "2"),
        Filing("ELECON", "2026-10-14T14:04:07+05:30", "routine", "Investor Presentation", "", "3"),
    ]
    obs = ms.observe(_obs_panel(), names, date(2026, 10, 14), market="nse", filings=filings)
    s = obs["summary"]
    assert s["movers"] == 2 and s["liquid_names"] == 3
    taril = next(m for m in obs["movers"] if m["symbol"] == "TARIL")
    elecon = next(m for m in obs["movers"] if m["symbol"] == "ELECON")
    assert taril["gap_part"] == pytest.approx(333 / 304 - 1, abs=1e-4)
    assert taril["filings_overnight"] == ["results"]
    assert elecon["gap_part"] == 0
    assert elecon["filings_intraday"] == [{"at": "13:19", "category": "results"}]  # routine dropped
    assert s["catchable_after_open"] == 2  # both still made >= 2% open->close
    assert s["with_results"] == 2


def test_forward_picks_must_be_registered_before_the_next_open(tmp_path) -> None:
    session = date(2026, 10, 14)
    panel = _panel(n_days=3, planted=False)
    panel["date"] = panel["date"].map(lambda d: session if d == max(panel["date"]) else d)
    ms.append_chain(
        tmp_path / "candidates.jsonl",
        {"event": "candidate_activated", "activated_at": "2026-10-14T19:00:00+05:30",
         "name": "vol_rel_top10", "target": "open_to_close_1d",
         "clauses": [{"feature": "vol_rel", "side": "top", "quantile": 0.1}]},
    )  # fmt: skip
    late = ms.register_picks(
        tmp_path, panel, {}, session, now=datetime(2026, 10, 15, 9, 15, tzinfo=IST)
    )
    assert late["status"] == "too_late"
    ok = ms.register_picks(
        tmp_path, panel, {}, session, now=datetime(2026, 10, 14, 19, 30, tzinfo=IST)
    )
    assert ok["status"] == "registered" and ok["candidates"] == 1
    again = ms.register_picks(
        tmp_path, panel, {}, session, now=datetime(2026, 10, 14, 20, 0, tzinfo=IST)
    )
    assert again["candidates"] == 0  # one registration per candidate per session
    rows, intact = ms.read_chain(tmp_path / "forward_picks.jsonl")
    assert intact and len(rows) == 1 and len(rows[0]["codes"]) == 5  # pct >= 0.9 of 40


def test_forward_scores_ignore_late_rows_and_use_the_same_day_null(tmp_path) -> None:
    session = date(2026, 10, 14)
    panel = pd.DataFrame(
        [
            {"date": session, "code": "A", "open_to_close_1d": 0.03, "open_to_close_5d": 0.0},
            {"date": session, "code": "B", "open_to_close_1d": -0.01, "open_to_close_5d": 0.0},
        ]
    )
    for registered, codes in (
        ("2026-10-14T19:30:00+05:30", ["A"]),
        ("2026-10-15T10:00:00+05:30", ["B"]),
    ):  # noqa: E501
        ms.append_chain(
            tmp_path / "forward_picks.jsonl",
            {"event": "picks_registered", "candidate": "c", "target": "open_to_close_1d",
             "decision_session": session.isoformat(), "registered_at": registered,
             "deadline": "2026-10-15T09:15:00+05:30", "codes": codes},
        )  # fmt: skip
    scores = ms.forward_scores(tmp_path, panel, COSTS)
    assert len(scores) == 1 and scores[0]["forward_days"] == 1
    assert scores[0]["mean_excess"] == pytest.approx(0.03 - 0.01)  # A vs mean(A, B)
    assert scores[0]["cleared_forward_bar"] is False  # one day is never enough


def test_crypto_session_is_complete_only_after_its_05_30_close() -> None:
    panel = pd.DataFrame({"date": [date(2026, 10, 13), date(2026, 10, 14)]})
    before = datetime(2026, 10, 15, 5, 0, tzinfo=IST)
    after = datetime(2026, 10, 15, 5, 45, tzinfo=IST)
    assert ms.latest_complete_session(panel, "crypto", before) == date(2026, 10, 13)
    assert ms.latest_complete_session(panel, "crypto", after) == date(2026, 10, 14)
    equity_mid = datetime(2026, 10, 14, 12, 0, tzinfo=IST)
    assert ms.latest_complete_session(panel, "nse", equity_mid) == date(2026, 10, 13)
    assert ms.next_open(date(2026, 10, 16)) - timedelta(days=3) == datetime(
        2026, 10, 16, 9, 15, tzinfo=IST
    )  # Friday -> Monday 09:15
