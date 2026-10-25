from __future__ import annotations

from tradedesk_lab.pre_results_hold import qualifies, summarise


def test_qualifies_requires_all_four_conditions() -> None:
    assert qualifies(0.06, 2.5, 3e8, 100.0)
    assert not qualifies(0.049, 2.5, 3e8, 100.0)
    assert not qualifies(0.06, 1.9, 3e8, 100.0)
    assert not qualifies(0.06, 2.5, 1e8, 100.0)
    assert not qualifies(0.06, 2.5, 3e8, 49.0)


def _row(q: float, wait: float, pre: float, key: int, year: int = 2023) -> dict[str, float]:
    return {"key": key, "year": year, "q": q, "wait": wait, "pre": pre, "runup": 0.01,
            "gap": 0.08 if q else -0.02, "days_pre": 12.0, "days_wait": 20.0,
            "big": 1.0}  # fmt: skip


def test_summarise_charges_wait_nothing_on_non_qualifiers() -> None:
    rows = [_row(1, 0.02, 0.10, k) for k in range(5)]
    rows += [_row(0, 0.0, -0.01, k) for k in range(5, 20)]
    out = summarise(rows)
    assert out["qualify_share"] == 5 / 20
    assert abs(out["big_universe"]["wait_per_event"]["mean"] - 0.02 * 5 / 20) < 1e-12
    assert out["big_universe"]["capital_days_per_event"]["wait"] == 20 * 5 / 20
    assert out["on_non_qualifiers"]["prebuy_net"]["mean"] == -0.01


def test_summarise_empty() -> None:
    assert summarise([]) == {"events": 0}
