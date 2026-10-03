from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from tradedesk_lab.accuracy_geometry import (
    DEFAULT_GEOMETRY_PROTOCOL,
    EntrySpec,
    PriceBar,
    _quick_outcome,
    chronological_partition,
    wilson_lower_bound,
)
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.outcomes import simulate_outcome

from tradedesk.config import load_config
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.markets.market import nse_market


def test_protocol_freezes_bounded_54_geometry_race() -> None:
    protocol = DEFAULT_GEOMETRY_PROTOCOL
    combinations = (
        len(protocol.entry_modes)
        * len(protocol.stop_atrs)
        * len(protocol.target_rs)
        * len(protocol.max_holds)
    )
    assert combinations == 54
    assert protocol.min_accuracy == 0.80
    assert protocol.min_wilson_lower == 0.70
    assert len(protocol.sha256) == 64


def test_chronological_partition_never_mixes_locked_session() -> None:
    rows = [
        {"signal_id": f"s{i}", "armed_on": date(2026, 1, i + 1)} for i in range(10)
    ]
    development, locked, split_at = chronological_partition(pd.DataFrame(rows), 0.20)
    assert len(development) == 8
    assert len(locked) == 2
    assert development["_armed_date"].max() < split_at
    assert locked["_armed_date"].min() >= split_at


@pytest.mark.parametrize(
    ("wins", "total", "expected"), ((0, 0, 0.0), (80, 100, 0.711), (11, 23, 0.292))
)
def test_wilson_lower_bound_is_conservative(
    wins: int, total: int, expected: float
) -> None:
    assert wilson_lower_bound(wins, total) == pytest.approx(expected, abs=0.002)


def test_fast_quick_outcome_matches_conservative_production_simulator() -> None:
    index = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    bars = pd.DataFrame(
        {
            "open": [100.0, 100.0, 104.0],
            "high": [101.0, 106.0, 105.0],
            "low": [99.0, 99.0, 103.0],
            "close": [100.0, 105.0, 104.0],
            "volume": [1000, 1000, 1000],
            "ema10": [99.0, 100.0, 101.0],
            "atr14": [5.0, 5.0, 5.0],
        },
        index=index,
    )
    entry = EntrySpec(
        on=index[0].date(),
        fill=100.0,
        at_open=True,
        future=tuple(
            PriceBar(
                on=stamp.date(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
            )
            for stamp, row in bars.iterrows()
        ),
    )
    costs = nse_market(load_config(ROOT)).costs
    fast = _quick_outcome(
        entry, stop=95.0, target=105.0, max_hold=2, qty=10, costs=costs
    )
    sig = Signal(
        id="parity",
        scrip_code="1",
        symbol="TEST",
        setup=SetupKind.BASE_BREAKOUT,
        armed_on=date(2026, 1, 2),
        trigger=100.0,
        stop=95.0,
        t1=105.0,
        t2=105.0,
        atr=5.0,
        exit_plan=ExitPlan(
            partial_at_r=1.0,
            partial_fraction=1.0,
            time_stop_sessions=2,
            time_stop_min_r=999.0,
            max_hold_sessions=2,
        ),
    )
    reference = simulate_outcome(
        sig, bars, index[0], 100.0, 10, costs, entry_at_open=True
    )
    assert fast is not None
    assert fast[0] == reference["label"]
    assert fast[1] == pytest.approx(reference["net_r"])
