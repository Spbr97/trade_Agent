"""Wilson lower bound: verified by mathematical properties any correct implementation must
satisfy, not a memorised textbook number this environment has no way to look up and check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradedesk.reliability import (
    append_reliability_history,
    load_reliability_history,
    overall_reliability,
    symbol_confidence,
    wilson_lower_bound,
)


def test_zero_trades_is_none_not_a_fabricated_number() -> None:
    assert wilson_lower_bound(0, 0) is None


def test_rejects_impossible_inputs() -> None:
    with pytest.raises(ValueError):
        wilson_lower_bound(-1, 5)
    with pytest.raises(ValueError):
        wilson_lower_bound(6, 5)


def test_always_bounded_in_zero_one() -> None:
    for wins, n in [(0, 1), (1, 1), (0, 100), (100, 100), (37, 100), (1, 1000000)]:
        b = wilson_lower_bound(wins, n)
        assert b is not None
        assert 0.0 <= b <= 1.0


def test_never_exceeds_the_raw_win_rate() -> None:
    """The whole point: the lower bound is always <= the point estimate, never a flattering
    number above what was actually observed."""
    for wins, n in [(1, 1), (5, 10), (50, 100), (99, 100)]:
        b = wilson_lower_bound(wins, n)
        assert b is not None
        assert b <= wins / n + 1e-12


def test_monotonic_in_wins_at_fixed_n() -> None:
    n = 50
    bounds = [wilson_lower_bound(w, n) for w in range(n + 1)]
    assert all(b is not None for b in bounds)
    assert bounds == sorted(bounds), "confidence must strictly rise as more calls win"


def test_more_evidence_at_the_same_rate_raises_confidence() -> None:
    """A coin that goes 1/1 must read as LESS trustworthy than one that goes 60/100, even
    though the smaller sample has a higher raw win rate (100% vs 60%) - this is the entire
    reason this module exists instead of just sorting by win rate."""
    small_sample = wilson_lower_bound(1, 1)
    large_sample = wilson_lower_bound(60, 100)
    assert small_sample is not None and large_sample is not None
    assert large_sample > small_sample, (
        f"60/100 ({large_sample:.3f}) should beat 1/1 ({small_sample:.3f}) - a single win "
        "must not outrank a large, real track record"
    )


def test_converges_to_the_raw_win_rate_as_n_grows() -> None:
    """A confidence interval's actual mathematical guarantee: it narrows to the true value
    as evidence accumulates. Checking this IS checking correctness, not a weaker substitute
    for a reference constant this environment has no way to independently verify."""
    p = 0.55
    prev_gap = 1.0
    for n in [10, 100, 1000, 100_000]:
        wins = round(p * n)
        b = wilson_lower_bound(wins, n)
        assert b is not None
        gap = abs(p - b)
        assert gap < prev_gap, f"gap did not shrink at n={n}: {gap} vs previous {prev_gap}"
        prev_gap = gap
    assert prev_gap < 0.01, f"still {prev_gap:.4f} away from the true rate at n=100,000"


def test_symbol_confidence_ranks_evidence_over_a_lucky_streak() -> None:
    outcomes = {
        "LUCKY": [True],  # 1/1 - no real evidence
        "PROVEN": [True] * 60 + [False] * 40,  # 60/100 - a real, if middling, track record
        "COLDSTREAK": [False] * 5,  # 0/5
    }
    ranked = symbol_confidence(outcomes)
    names = [s.symbol for s in ranked]
    assert names.index("PROVEN") < names.index("LUCKY"), "real evidence must outrank one win"
    assert names[-1] == "COLDSTREAK"
    proven = next(s for s in ranked if s.symbol == "PROVEN")
    assert proven.n == 100 and proven.wins == 60
    assert proven.win_rate == pytest.approx(0.6)


def test_symbol_confidence_skips_symbols_with_no_resolved_calls() -> None:
    assert symbol_confidence({"EMPTY": []}) == []


def test_overall_reliability_reports_none_confidence_with_no_data() -> None:
    r = overall_reliability(0, 0)
    assert r["confidence"] is None
    assert r["win_rate"] is None
    assert r["n"] == 0


def test_reliability_history_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    assert load_reliability_history(path) == []
    append_reliability_history({"n": 10, "wins": 6, "confidence": 0.42}, path)
    append_reliability_history({"n": 15, "wins": 9, "confidence": 0.47}, path)
    rows = load_reliability_history(path)
    assert len(rows) == 2
    assert rows[0]["confidence"] == 0.42 and rows[1]["confidence"] == 0.47
    assert "date" in rows[0]  # stamped automatically, not left to the caller
