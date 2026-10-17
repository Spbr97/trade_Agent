from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from tradedesk_lab import multiday_hold_study as study


def _bars(closes: list[float], start: date = date(2026, 10, 1)) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [c - 1.0 for c in closes],
            "close": closes,
            "session": [start + timedelta(days=i) for i in range(len(closes))],
        }
    )


def test_hold_returns_enter_next_open_and_exit_hth_close_without_look_ahead() -> None:
    bars = {"A": _bars([100.0, 110.0, 120.0, 130.0])}
    calls = [{"rule": "r", "scrip_code": "A", "armed_on": "2026-10-01"}]

    out = study.hold_returns(calls, bars, holds=(1, 3, 5))

    # armed on the 1st's close -> entry is the 2nd's open (109); hold 1 exits the 2nd's
    # close, hold 3 the 4th's; hold 5 has no exit bar yet, so it is not graded.
    assert out.set_index("hold")["gross"].to_dict() == pytest.approx(
        {1: 110 / 109 - 1, 3: 130 / 109 - 1}
    )


def test_summarize_is_day_clustered_and_compares_to_the_control_on_shared_days() -> None:
    rows = []
    for day in range(6):
        armed = f"2026-10-{day + 1:02d}"
        rows += [{"rule": "rule", "scrip_code": c, "armed_on": armed, "hold": 3,
                  "gross": 0.02 + 0.001 * day} for c in ("A", "B", "C")]  # fmt: skip
        rows.append({"rule": study.CONTROL, "scrip_code": "Z", "armed_on": armed, "hold": 3,
                     "gross": 0.005 * (day % 2)})  # fmt: skip

    summary = {row["rule"]: row for row in study.summarize(pd.DataFrame(rows), cost=0.004)}

    rule = summary["rule"]
    assert (rule["picks"], rule["days"], rule["shared_days"]) == (18, 6, 6)
    assert rule["net_mean"] == pytest.approx(0.0185)
    assert rule["vs_control_mean"] == pytest.approx(0.0185 - 0.0025 + 0.004)
    assert rule["vs_control_t"] is not None and rule["vs_control_t"] > 0
    assert summary[study.CONTROL]["vs_control_mean"] is None


def test_too_few_days_reports_no_t() -> None:
    rows = pd.DataFrame(
        [{"rule": "rule", "scrip_code": "A", "armed_on": "2026-10-01", "hold": 1, "gross": 0.01}]
    )
    (row,) = study.summarize(rows, cost=0.0)
    assert row["day_net_t"] is None


def test_hedged_book_subtracts_the_index_over_the_same_entry_and_exit_sessions() -> None:
    bars = {"A": _bars([100.0, 110.0, 120.0, 130.0])}
    index = _bars([1000.0, 1010.0, 1020.0, 1030.0])
    calls = [{"rule": "r", "scrip_code": "A", "armed_on": "2026-10-01"}]

    out = study.hold_returns(calls, bars, holds=(3,), index=index)
    net = study.books_for("nse", 0.004)["hedged"](out)

    stock, nifty = 130 / 109 - 1, 1030 / 1009 - 1
    expected = stock - nifty - 0.004 - study.INDEX_FUTURES_ROUND_TRIP
    assert float(net.iloc[0]) == pytest.approx(expected)


def test_crypto_futures_books_charge_fees_and_funding_to_both_sides() -> None:
    returns = pd.DataFrame([{"gross": 0.05, "hold": 5, "index_gross": float("nan")}])
    books = study.books_for("crypto", 0.0157)
    drag = study.CRYPTO_FUTURES_ROUND_TRIP + 5 * study.CRYPTO_FUNDING_PER_DAY

    assert float(books["fut_long"](returns).iloc[0]) == pytest.approx(0.05 - drag)
    assert float(books["fut_short"](returns).iloc[0]) == pytest.approx(-0.05 - drag)
    assert float(books["spot_long"](returns).iloc[0]) == pytest.approx(0.05 - 0.0157)
