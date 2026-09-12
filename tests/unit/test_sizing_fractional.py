"""Fractional position sizing (M13 follow-up).

Why this exists: every BTC/ETH/BNB signal was being skipped as "size 0" because
`position_size` floored to whole units, so a Rs 250 risk budget against BTC's ~Rs 77 lakh
price rounded to nothing. Discovered 2026-09-12 by checking which codes actually produced
trades in the "majors" crypto backtest - BTC, ETH and BNB produced none, ever, so the
headline expectancy was really a verdict on cheap altcoins only.

The NSE guarantee is the important half of these tests: step 1.0 must reproduce the old
math.floor path exactly, because the same sizing code backs live NSE trading.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from tradedesk.risk.sizing import SizeInputs, position_size, quantize_qty

CRYPTO = dict(qty_step=1e-8, min_notional=100.0)


def _old_whole_unit_qty(equity: float, entry: float, stop: float, gap95: float | None) -> int:
    """The pre-step algorithm, verbatim, as the reference NSE must still match."""
    qty = math.floor(equity * 0.0025 / (entry - stop))
    if qty * entry > equity * 0.25:
        qty = math.floor(equity * 0.25 / entry)
    if gap95:
        gap_qty = math.floor(equity * 0.01 / (entry * gap95))
        qty = min(qty, gap_qty)
    return max(qty, 0)


# ------------------------------------------------------------------ quantize_qty


@pytest.mark.parametrize(
    "qty, step, expected",
    [
        (3.7, 1.0, 3.0),  # whole units floor exactly as before
        (0.00322804123, 1e-8, 0.00322804),
        (357.9, 1.0, 357.0),
        (0.5, 1.0, 0.0),  # a sub-unit quantity is genuinely zero at step 1
    ],
)
def test_quantize_qty_floors_to_the_step(qty: float, step: float, expected: float) -> None:
    assert quantize_qty(qty, step) == expected


def test_quantize_qty_returns_clean_decimals_not_float_noise() -> None:
    # 0.0032200000000000003 would be the naive float answer.
    assert repr(quantize_qty(0.00322, 1e-8)) == "0.00322"


def test_quantize_qty_rejects_a_non_positive_step() -> None:
    with pytest.raises(ValueError, match="step"):
        quantize_qty(1.0, 0.0)


# --------------------------------------------------------- NSE must not move


@given(
    equity=st.floats(min_value=10_000, max_value=5_000_000),
    entry=st.floats(min_value=5, max_value=5_000),
    stop_frac=st.floats(min_value=0.80, max_value=0.995),
    # Either "no gap history" or a realistic percentile. Denormals like 1e-309 are
    # excluded because they overflow the plain-float reference below - position_size
    # itself handles them (Decimal -> an infinite, therefore non-binding, gap cap).
    gap95=st.one_of(st.just(0.0), st.floats(min_value=0.001, max_value=0.06)),
)
@hyp_settings(max_examples=400)
def test_step_one_reproduces_the_old_whole_unit_sizing(
    equity: float, entry: float, stop_frac: float, gap95: float
) -> None:
    stop = entry * stop_frac
    got = position_size(
        SizeInputs(
            equity=equity,
            entry=entry,
            stop=stop,
            max_risk_pct=0.0025,
            max_position_value_pct=0.25,
            gap_risk_cap_pct=0.01,
            gap95_pct=gap95,
        )
    ).qty
    assert got == _old_whole_unit_qty(equity, entry, stop, gap95)


def test_nse_default_step_is_whole_units() -> None:
    x = SizeInputs(equity=1.0, entry=1.0, stop=0.5, max_risk_pct=0.1, max_position_value_pct=1.0)
    assert x.qty_step == 1.0
    assert x.min_notional == 0.0


# ------------------------------------------------------------- crypto sizing


def test_btc_is_sizeable_at_all_which_it_was_not_before() -> None:
    """The regression this whole change exists for: at step 1.0 this returns qty 0."""
    args = dict(
        equity=100_000.0,
        entry=7_744_620.70,
        stop=7_670_299.80,
        max_risk_pct=0.0025,
        max_position_value_pct=0.25,
    )
    assert position_size(SizeInputs(**args)).qty == 0  # whole units: the old bug
    frac = position_size(SizeInputs(**args, **CRYPTO))
    assert frac.qty > 0
    assert frac.viable
    # capped by max position value (25% of equity), not by the risk budget
    assert frac.position_value == pytest.approx(25_000, rel=1e-3)


def test_fractional_risk_amount_respects_the_risk_budget() -> None:
    r = position_size(
        SizeInputs(
            equity=100_000.0,
            entry=252_693.20,
            stop=249_810.20,
            max_risk_pct=0.0025,
            max_position_value_pct=0.25,
            **CRYPTO,
        )
    )
    assert r.risk_amount <= 250.0 + 1e-6
    assert r.risk_amount == pytest.approx(250.0, rel=1e-3)


def test_below_min_notional_is_rejected_rather_than_sized_up() -> None:
    """CoinDCX's flat Rs 100 minimum: too small to place is qty 0, never a quantity
    quietly inflated past the risk budget to reach the minimum."""
    r = position_size(
        SizeInputs(
            equity=100.0,  # tiny book -> a Rs 0.25 risk budget
            entry=7_744_620.70,
            stop=7_670_299.80,
            max_risk_pct=0.0025,
            max_position_value_pct=0.25,
            **CRYPTO,
        )
    )
    assert r.qty == 0
    assert not r.viable
    assert any("min notional" in c for c in r.caps)
