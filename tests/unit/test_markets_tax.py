"""markets/tax.py and the after-tax gate in candidate_verdict."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradedesk.markets.tax import after_tax_r, mean_after_tax_r, tds_share_of_costs
from tradedesk.self_review import detector_authoring as da

SHARE = tds_share_of_costs(maker_taker_pct=0.002, tds_pct=0.01, gst_pct=0.18, slippage_pct=0.0005)


def _t(gross: float, net: float) -> SimpleNamespace:
    return SimpleNamespace(gross_r=gross, net_r=net)


def test_tds_is_about_two_thirds_of_crypto_round_trip_costs() -> None:
    assert 0.6 < SHARE < 0.7


def test_a_winner_is_taxed_on_its_gross_gain_and_a_loser_is_not_refunded() -> None:
    win = after_tax_r(_t(2.0, 1.9), SHARE)
    assert win == pytest.approx(1.9 + SHARE * 0.1 - 0.30 * 2.0)
    loss = after_tax_r(_t(-1.0, -1.1), SHARE)
    assert loss == pytest.approx(-1.1 + SHARE * 0.1)  # TDS back, no tax, no offset


def test_low_win_rate_trend_shape_is_negative_after_tax_despite_positive_net() -> None:
    # 39% winners at +2.05R gross, 61% losers at -1R gross, ~0.1R costs per trade
    trades = [_t(2.05, 1.95)] * 39 + [_t(-1.0, -1.1)] * 61
    assert sum(t.net_r for t in trades) / 100 > 0.0  # positive after fees and TDS
    assert mean_after_tax_r(trades, SHARE) < 0.0  # but not after the 30% no-offset tax


def test_high_win_rate_small_gain_shape_survives_tax() -> None:
    # 75% winners at +0.6R gross vs 25% losers at -1R: gross edge 0.20R clears the tax
    trades = [_t(0.6, 0.5)] * 75 + [_t(-1.0, -1.1)] * 25
    assert mean_after_tax_r(trades, SHARE) > 0.0


def test_empty_trades_have_no_after_tax_value() -> None:
    assert mean_after_tax_r([], SHARE) is None


def _report():
    return SimpleNamespace(
        stopped_at=None, kill_criteria={"passed": True},
        stages={"random_entry_benchmark": {"p_value": 0.0}, "in_sample": {"net_expectancy_r": 0.1}},
    )  # fmt: skip


def test_verdict_rejects_a_pattern_that_is_negative_after_tax() -> None:
    ok, reasons = da.candidate_verdict(_report(), True, after_tax_r=-0.05)
    assert not ok and any("after-tax" in r for r in reasons)
    assert da.candidate_verdict(_report(), True, after_tax_r=0.02)[0]
    assert da.candidate_verdict(_report(), True, after_tax_r=None)[0]  # untaxed market: no gate
