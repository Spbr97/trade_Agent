"""straddle_model: prices must be right, and the replay must follow the live rule."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from tradedesk_lab import option_income as oi
from tradedesk_lab import straddle_model as sm

COSTS = oi.OptionCosts(brokerage_per_order=0.0, stt_sell_pct=0.0, exchange_pct=0.0, sebi_pct=0.0,
                       stamp_buy_pct=0.0)  # fmt: skip


def test_put_call_parity_holds() -> None:
    ce = sm.price("ce", 24000, 24000, 7 / 365, 0.15)
    pe = sm.price("pe", 24000, 24000, 7 / 365, 0.15)
    assert ce - pe == pytest.approx(24000 - 24000 * np.exp(-sm.RATE * 7 / 365), rel=1e-6)


def test_a_known_atm_price() -> None:
    # ATM is close to 0.4 x S x vol x sqrt(T)
    got = sm.price("ce", 24000, 24000, 7 / 365, 0.15)
    assert 0.4 * 24000 * 0.15 * (7 / 365) ** 0.5 == pytest.approx(got, rel=0.08)


def test_at_expiry_an_option_is_worth_its_intrinsic_value() -> None:
    assert sm.price("ce", 24100, 24000, 0, 0.15) == 100
    assert sm.price("pe", 24100, 24000, 0, 0.15) == 0


def test_implied_vol_inverts_the_price() -> None:
    value = sm.price("pe", 24000, 24050, 5 / 365, 0.13)
    assert sm.implied_vol("pe", value, 24000, 24050, 5 / 365) == pytest.approx(0.13, abs=1e-4)
    assert sm.implied_vol("ce", 99999, 24000, 24050, 5 / 365) is None


@pytest.mark.parametrize(
    ("day", "expiry"),
    [
        (date(2024, 6, 3), date(2024, 6, 6)),  # Monday, Thursday expiry era
        (date(2024, 6, 6), date(2024, 6, 6)),  # the expiry day trades that expiry
        (date(2024, 6, 7), date(2024, 6, 13)),
        (date(2025, 9, 1), date(2025, 9, 2)),  # the Tuesday era
        (date(2026, 11, 4), date(2026, 11, 10)),
    ],
)
def test_the_nearest_expiry(day: date, expiry: date) -> None:
    assert sm.next_expiry(day) == expiry


def test_time_left_runs_out_at_the_expiry_close() -> None:
    day = date(2026, 11, 3)
    assert sm.years_left(day, 23, day) == pytest.approx(15 / sm.MINUTES_A_YEAR)
    assert sm.years_left(day, 24, day) == 0.0


def _session(path: list[float], width: float = 0.0, vix: float = 0.14) -> sm.Session:
    """A flat day except for the 25 closes given; highs and lows hug each bar by `width`."""

    c = np.array(path, float)
    o = np.concatenate(([c[0]], c[:-1]))
    h = np.maximum(o, c) + width
    low = np.minimum(o, c) - width
    return sm.Session(date(2026, 11, 2), o, h, low, c, np.full(25, vix))


def test_a_flat_day_earns_time_decay_on_both_legs() -> None:
    got = sm.replay(_session([24000.0] * 25), 0.30, 1.0, COSTS)
    assert got["stopped"] == 0
    assert got["net"] > 0


def test_a_trending_day_stops_one_leg_and_the_other_pays_more() -> None:
    path = list(np.linspace(24000, 24600, 25))
    got = sm.replay(_session(path), 0.30, 1.0, COSTS)
    assert got["stopped"] == 1
    assert got["net"] > 0


def test_a_whipsaw_stops_both_legs_and_loses() -> None:
    path = [24000.0] * 3 + list(np.linspace(24000, 24250, 6)) + list(np.linspace(24250, 23700, 8))
    path += [23700.0] * (25 - len(path))
    got = sm.replay(_session(path), 0.30, 1.0, COSTS)
    assert got["stopped"] == 2
    assert got["net"] < 0


def test_a_bar_opening_past_the_stop_fills_at_that_open_not_the_stop() -> None:
    # The whole jump is between bars, so the fill must cost more than the stop allows.
    gap = [24000.0] * 5 + [24800.0] * 20
    c = np.array(gap)
    o = c.copy()
    s = sm.Session(date(2026, 11, 2), o, c, c, c, np.full(25, 0.14))
    got = sm.replay(s, 0.30, 1.0, COSTS)
    premium = got["premium"] / 2
    assert got["net"] < -(0.30 * premium * oi.LOT)


def test_a_wider_stop_is_not_hit_as_soon() -> None:
    path = list(np.linspace(24000, 24120, 25))
    assert sm.replay(_session(path), 0.30, 1.0, COSTS)["stopped"] >= \
        sm.replay(_session(path), 0.40, 1.0, COSTS)["stopped"]  # fmt: skip


def test_a_thinner_premium_earns_less() -> None:
    flat = _session([24000.0] * 25)
    assert sm.replay(flat, 0.30, 0.9, COSTS)["net"] < sm.replay(flat, 0.30, 1.1, COSTS)["net"]


def test_worst_fall_and_time_under_water() -> None:
    values = [10.0, -4.0, -3.0, 5.0, 3.0, -1.0]
    assert sm.worst_fall(values) == pytest.approx(-7.0)
    assert sm.longest_under_water(values) == 3


def _summary(**kw: object) -> dict:
    base = {"mean_net": 100.0, "by_year": {"2025": {"sessions": 200, "net": 1.0}},
            "worst_fall": -1000.0, "worst_day": -1000.0}  # fmt: skip
    return {**base, **kw}


def test_the_verdict_lines() -> None:
    assert sm.verdict(_summary(), _summary())["supports"]
    assert not sm.verdict(_summary(worst_fall=-70_000.0), _summary())["supports"]
    assert not sm.verdict(_summary(worst_day=-30_000.0), _summary())["supports"]
    assert not sm.verdict(_summary(), _summary(mean_net=-1.0))["supports"]
    lost = {"2025": {"sessions": 200, "net": -1.0}}
    assert not sm.verdict(_summary(by_year=lost), _summary())["supports"]
    few = {"2024": {"sessions": 50, "net": -1.0}}
    assert sm.verdict(_summary(by_year=few), _summary())["supports"]
