"""Potential-movers screen: causal features, honest adoption rule, forward-only ledger."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.potential_movers import (
    build_panel,
    forward_valid,
    resolve_forward,
    tier_statistics,
)
from tradedesk.potential_movers_model import split_dates, top_k_by_session


def _frame(n: int = 400, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    start = int(datetime(2024, 1, 1, tzinfo=IST).timestamp())
    return pd.DataFrame(
        {
            "scrip_code": "NSE_1",
            "ts": [start + i * 86400 for i in range(n)],
            "open": close * (1 + rng.normal(0, 0.003, n)),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
        }
    )


def test_features_use_only_data_up_to_each_close() -> None:
    frame = _frame()
    full = build_panel(frame, "nse")
    cut = build_panel(frame.iloc[:300], "nse")
    probe = cut["date"].iloc[-5]
    a = full[full["date"] == probe].iloc[0]
    b = cut[cut["date"] == probe].iloc[0]
    for column in ("below50", "pos6m", "d5", "rng_rel", "vol_rel", "gap", "ret20"):
        assert a[column] == b[column]
    # only the label looks forward
    assert not np.isclose(a["next_ret"], 0.0)


def test_tier_statistics_only_use_sessions_whose_next_day_is_known() -> None:
    panel = build_panel(_frame(), "nse")
    as_of = panel["date"].iloc[-1]
    stats = tier_statistics(panel, as_of)
    known = panel[panel["next_ret"].notna() & (panel["date"] < as_of)]
    assert stats["base"]["n"] == len(known)


def test_split_is_chronological_and_purged() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(1000)]
    parts = split_dates(days)
    assert parts is not None
    train, val, locked = parts["train"], parts["validation"], parts["locked"]
    assert train[-1] < val[0] < val[-1] < locked[0]
    assert (parts["validation"][0] - parts["train"][-1]).days > 3
    assert split_dates(days[:300]) is None


def test_top_k_is_per_session() -> None:
    frame = pd.DataFrame(
        {
            "date": [date(2026, 1, 1)] * 40 + [date(2026, 1, 2)] * 40,
            "tier": [""] * 80,
            "below50": 0.0,
            "next_ret": 0.0,
        }
    )
    picks = top_k_by_session(frame, pd.Series(range(80)))
    assert picks.groupby("date").size().tolist() == [25, 25]


def test_late_lists_are_not_forward_evidence() -> None:
    friday = date(2026, 10, 9)
    assert forward_valid("nse", friday, datetime(2026, 10, 12, 8, 0, tzinfo=IST))
    assert not forward_valid("nse", friday, datetime(2026, 10, 12, 9, 30, tzinfo=IST))
    assert not forward_valid("nse", friday, datetime(2026, 10, 13, 8, 0, tzinfo=IST))


def test_forward_resolution_compares_against_same_day_universe() -> None:
    panel = pd.DataFrame(
        {
            "code": ["A", "B"],
            "date": [date(2026, 1, 5)] * 2,
            "next_ret": [0.08, -0.01],
        }
    )
    entry = {"as_of": "2026-01-05", "list": [{"scrip_code": "A"}, {"scrip_code": "B"}]}
    out = resolve_forward(panel, [entry], {"2026-01-05": 0.25})
    assert out["picks"] == 2 and out["up5_rate"] == 0.5
    assert out["lift_over_universe"] == 2.0
