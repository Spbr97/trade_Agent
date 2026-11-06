"""ipo_forward: only new listings count, inside the band, each recorded once."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import convergence_forward as cf
from tradedesk_lab import ipo_forward as fw
from tradedesk_lab import ipo_listing as il
from tradedesk_lab import new_listings as nl

NOW = datetime(2027, 1, 1, 21, 0)
DAYS = list(pd.date_range("2026-11-02", periods=40, freq="B").date)
LISTED = date(2026, 11, 9)
ROW = DAYS.index(LISTED)


def _panels(opening: float = 120.0, stop: int = 40) -> nl.Panels:
    days = DAYS[:stop]
    closes = pd.DataFrame({"X": [np.nan] * ROW + [130.0] * (stop - ROW)}, index=days)
    opens = closes.copy()
    opens.iloc[ROW, 0] = opening
    return nl.Panels(opens, closes, closes, closes * 0 + 1e6)


def _rows(ipos: list[dict], known: dict | None = None, panels: nl.Panels | None = None) -> list:
    return fw.new_rows(ipos, known or {}, panels if panels is not None else _panels(), {"X": "X"},
                       lambda a, b: 0.005, lambda e, x: 0.01, NOW)  # fmt: skip


IPO = {"symbol": "X", "issue": 100.0, "listed": LISTED}


def test_an_open_in_the_band_is_a_call_closed_at_the_twentieth_close() -> None:
    (call, closed) = _rows([IPO])
    assert call["entry"] == pytest.approx(120.0 * (1 + il.SLIPPAGE))
    assert call["gain"] == pytest.approx(0.20)
    assert closed["exit_on"] == str(DAYS[ROW + 19])
    sold = 130.0 * (1 - il.SLIPPAGE)
    assert closed["net"] == pytest.approx(sold / call["entry"] - 1 - 0.005)
    assert closed["excess"] == pytest.approx(closed["net"] - 0.01)


@pytest.mark.parametrize("opening", [105.0, 109.9, 130.0, 150.0, 90.0])
def test_an_open_outside_the_band_is_not_a_call(opening: float) -> None:
    assert _rows([IPO], panels=_panels(opening)) == []


def test_the_band_edges() -> None:
    assert _rows([IPO], panels=_panels(110.0))
    assert _rows([IPO], panels=_panels(129.99))


def test_a_listing_before_the_trial_does_not_count() -> None:
    assert _rows([{**IPO, "listed": date(2026, 11, 6)}]) == []


def test_a_call_stays_open_until_its_twentieth_session_exists() -> None:
    (call,) = _rows([IPO], panels=_panels(stop=ROW + 19))
    assert call["event"] == "call"


def test_nothing_is_recorded_twice() -> None:
    first = _rows([IPO])
    assert _rows([IPO], known=cf.fold(first)) == []


def test_a_call_made_earlier_closes_later() -> None:
    (call,) = _rows([IPO], panels=_panels(stop=ROW + 19))
    (closed,) = _rows([IPO], known=cf.fold([call]))
    assert closed["event"] == "closed"


def test_a_listing_with_no_price_yet_waits() -> None:
    assert _rows([{**IPO, "listed": date(2027, 6, 1)}]) == []
