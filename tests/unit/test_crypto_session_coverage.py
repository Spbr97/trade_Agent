"""A few active pairs with no closed-session bar drop out; many still fail closed."""

from __future__ import annotations

from tradedesk.markets.crypto_universe import session_coverage


def test_a_new_listing_without_a_bar_drops_out_instead_of_failing() -> None:
    universe = [f"CDX_{i}INR" for i in range(339)]
    covered = [c for c in universe if c != "CDX_7INR"]
    dropped, failure = session_coverage(universe, covered)
    assert dropped == ["CDX_7INR"] and failure is None


def test_widespread_gaps_still_fail_closed() -> None:
    universe = [f"CDX_{i}INR" for i in range(339)]
    allowance = max(3, int(0.02 * 339))  # 6
    covered = universe[allowance + 1 :]
    dropped, failure = session_coverage(universe, covered)
    assert len(dropped) == allowance + 1
    assert failure == f"closed-session coverage: {339 - allowance - 1}/339"


def test_small_universes_keep_a_floor_of_three() -> None:
    universe = ["A", "B", "C", "D", "E"]
    assert session_coverage(universe, ["A", "B"])[1] is None  # 3 dropped: allowed
    assert session_coverage(universe, ["A"])[1] is not None  # 4 dropped: fail closed
