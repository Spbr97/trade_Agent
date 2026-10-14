"""run_post_earnings_drift.py: the signal is decided at close(E+1) and filled at open(E+2), so
nothing at or after the fill may change whether or where a trade happens."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from tradedesk_lab.harness.run_post_earnings_drift import (
    DriftParams,
    build_stock_bars,
    build_trades,
    find_signals,
    market_two_day_median,
    quiet_mask,
    resolve,
)


class _FakeRoundTrip:
    def __init__(self, total: Decimal) -> None:
        self.total = total


class _FakeCosts:
    def round_trip_cost(self, *, trade_type, qty, entry_price, exit_price):
        notional = (Decimal(str(entry_price)) + Decimal(str(exit_price))) * Decimal(str(qty))
        return _FakeRoundTrip(total=notional * Decimal("0.0005"))


DAYS = list(pd.bdate_range("2026-01-05", periods=60).date)
EVENT = 40  # position of the results day in DAYS


def _frame(closes: list[float], days: list[date] = DAYS) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": days,
            "open": c,
            "high": c + 1.0,
            "low": c - 1.0,
            "close": c,
            "volume": np.full(len(c), 400_000.0),  # Rs 4 crore a day at ~100: liquid
        }
    )


def _jumper() -> list[float]:
    closes = [100.0] * len(DAYS)
    for i in range(EVENT + 1, len(DAYS)):
        closes[i] = 110.0  # +10% reaction at E+1, then flat
    return closes


def _universe(jumper: list[float] | None = None) -> dict:
    stocks = {"NSE_J": build_stock_bars(_frame(jumper or _jumper()), "NSE_J", [DAYS[EVENT]])}
    for k in range(3):  # flat peers so the market median is ~0
        stocks[f"NSE_F{k}"] = build_stock_bars(_frame([100.0] * len(DAYS)), f"NSE_F{k}", [])
    return stocks


def _build(stocks, params=None, risk_rupees=250.0):
    return build_trades(
        stocks,
        market_two_day_median(stocks),
        params or DriftParams(),
        costs=_FakeCosts(),
        risk_rupees=risk_rupees,
        slip=0.0005,
    )


def test_strong_positive_reaction_trades_at_the_next_open() -> None:
    trades, counts = _build(_universe())

    assert counts["signals"] == 1 and len(trades) == 1
    t = trades[0]
    assert t.decision_date == DAYS[EVENT + 1].isoformat()
    assert t.entry_date == DAYS[EVENT + 2].isoformat()
    assert t.entry == 110.0
    assert abs(t.reaction - 0.10) < 1e-9


def test_nothing_from_the_fill_onwards_changes_the_signal_entry_or_stop() -> None:
    base = _universe()
    altered_closes = _jumper()
    for i in range(EVENT + 3, len(DAYS)):
        altered_closes[i] = 60.0  # collapse after the fill
    altered = _universe(altered_closes)

    params = DriftParams()
    sig_base = find_signals(base, market_two_day_median(base), params)
    sig_alt = find_signals(altered, market_two_day_median(altered), params)
    assert sig_base == sig_alt
    entry_b, stop_b, *_ = resolve(base["NSE_J"], EVENT + 2, EVENT + 1, params)
    entry_a, stop_a, *_ = resolve(altered["NSE_J"], EVENT + 2, EVENT + 1, params)
    assert (entry_b, stop_b) == (entry_a, stop_a)


def test_gap_below_the_stop_exits_at_that_open() -> None:
    closes = _jumper()
    closes[EVENT + 3] = 80.0  # opens far below any 2xATR stop the day after entry
    stocks = _universe(closes)
    trades, _ = _build(stocks)

    t = trades[0]
    assert t.exit_date == DAYS[EVENT + 3].isoformat()
    assert abs(t.gross_r - (80.0 - t.entry) / (t.entry - t.stop)) < 1e-9
    assert t.gross_r < -1.0  # a gap through the stop loses more than 1R


def test_results_on_a_weekend_map_to_the_next_session() -> None:
    saturday = DAYS[EVENT] - timedelta(days=DAYS[EVENT].weekday() - 5 + 7)
    bars = build_stock_bars(_frame(_jumper()), "NSE_J", [saturday])
    assert bars is not None
    assert DAYS[bars.results_pos[0]] > saturday
    assert DAYS[bars.results_pos[0] - 1] < saturday


def test_event_without_a_full_holding_window_is_counted_not_traded() -> None:
    days = DAYS[: EVENT + 5]  # E+2 exists, E+2+hold-1 does not
    closes = _jumper()[: EVENT + 5]
    stocks = {"NSE_J": build_stock_bars(_frame(closes, days), "NSE_J", [days[EVENT]])}
    for k in range(3):
        stocks[f"NSE_F{k}"] = build_stock_bars(_frame([100.0] * len(days), days), f"NSE_F{k}", [])
    trades, counts = _build(stocks)

    assert trades == [] and counts["incomplete"] == 1


def test_trade_that_cannot_be_sized_inside_policy_risk_is_excluded() -> None:
    trades, counts = _build(_universe(), risk_rupees=1.0)
    assert trades == [] and counts["unsizable"] == 1


def test_quiet_mask_keeps_null_entries_clear_of_results() -> None:
    bars = build_stock_bars(_frame(_jumper()), "NSE_J", [DAYS[EVENT]])
    quiet = quiet_mask(bars, hold=10)
    assert not quiet[EVENT]
    assert not quiet[EVENT - 10 - 15]  # its holding window would reach the results day
    assert quiet[EVENT - 10 - 16]
    assert not quiet[EVENT + 15]
    assert quiet[EVENT + 16]
