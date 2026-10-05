from __future__ import annotations

from datetime import date

from tradedesk.research_tracker import MAX_PER_RULE, _stable_new_hits


def _hits(count: int) -> list[tuple[str, str, float, float]]:
    return [
        (f"BSE_{index:03d}", f"S{index:03d}", 100.0 + index, 2.0)
        for index in range(count)
    ]


def test_research_sample_is_stable_across_input_order() -> None:
    armed_on = date(2026, 10, 5)
    forward = _stable_new_hits("rsi2_dip_ema50", armed_on, _hits(40), set())
    reverse = _stable_new_hits("rsi2_dip_ema50", armed_on, list(reversed(_hits(40))), set())

    assert forward == reverse
    assert len(forward) == MAX_PER_RULE


def test_existing_rows_count_against_session_cap() -> None:
    rule = "rsi2_dip_ema50"
    armed_on = date(2026, 10, 5)
    existing = {
        f"{rule}:LEGACY_{index:03d}:{armed_on.isoformat()}"
        for index in range(MAX_PER_RULE - 2)
    }

    selected = _stable_new_hits(rule, armed_on, _hits(40), existing)

    assert len(selected) == 2
    selected_ids = {
        f"{rule}:{code}:{armed_on.isoformat()}" for code, _symbol, _close, _atr in selected
    }
    assert not selected_ids & existing


def test_full_existing_session_never_appends_a_second_sample() -> None:
    rule = "random_eligible"
    armed_on = date(2026, 10, 5)
    existing = {
        f"{rule}:OLD_{index:03d}:{armed_on.isoformat()}" for index in range(MAX_PER_RULE)
    }

    assert _stable_new_hits(rule, armed_on, _hits(40), existing) == []
