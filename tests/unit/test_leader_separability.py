from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.leader_discovery import FEATURE_COLUMNS
from tradedesk.leader_separability import (
    SESSION_COUNT,
    _replay_one,
    _select,
    _split_manifest,
    load_latest_separability,
    run_leader_separability,
    validate_separability_report,
)


def _synthetic_source() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = date(2026, 1, 1)
    for session_index in range(SESSION_COUNT):
        session = start + timedelta(days=session_index)
        for instrument in range(10):
            leader = instrument == session_index % 10
            row: dict[str, object] = {
                "session_date": session,
                "scrip_code": f"NSE_{instrument}",
                "symbol": f"S{instrument}",
                "opportunity_label": leader,
            }
            for feature_index, feature in enumerate(FEATURE_COLUMNS):
                row[feature] = (
                    float(leader) * (1.0 + feature_index / 100)
                    + instrument / 1000
                    + session_index / 100_000
                )
            rows.append(row)
    return pd.DataFrame(rows)


def _paths(test: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in test.to_dict(orient="records"):
        leader = bool(row["label"])
        rows.append(
            {
                "session_date": date.fromisoformat(str(row["session"])),
                "session": row["session"],
                "scrip_code": row["scrip_code"],
                "close": 100.0,
                "open_1": 100.0,
                "high_1": 102.0 if leader else 100.5,
                "low_1": 99.0,
                "close_1": 100.0,
                "open_2": 100.0,
                "high_2": 100.5,
                "low_2": 99.0,
                "close_2": 100.0,
                "open_3": 100.0,
                "high_3": 100.5,
                "low_3": 99.0,
                "close_3": 99.5,
            }
        )
    return pd.DataFrame(rows)


class _FreeCosts:
    slippage_pct = Decimal("0")

    def net_r_multiple(self, **kwargs) -> Decimal:  # type: ignore[no-untyped-def]
        return (kwargs["exit_price"] - kwargs["entry"]) / (
            kwargs["entry"] - kwargs["stop"]
        )


def test_frozen_split_has_purges_and_locked_test() -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(SESSION_COUNT)]
    split = _split_manifest(sessions)

    assert len(split.train) == 102
    assert len(split.purge_after_train) == 3
    assert len(split.validation) == 36
    assert len(split.purge_after_validation) == 3
    assert len(split.locked_test) == 36
    assert split.train[-1] < split.purge_after_train[0] < split.validation[0]
    assert split.validation[-1] < split.purge_after_validation[0] < split.locked_test[0]


def test_top_selection_is_deterministic_and_session_scoped() -> None:
    frame = pd.DataFrame(
        [
            {"session": "2026-01-01", "scrip_code": "B", "symbol": "B", "label": 0},
            {"session": "2026-01-01", "scrip_code": "A", "symbol": "A", "label": 1},
            {"session": "2026-01-02", "scrip_code": "C", "symbol": "C", "label": 1},
            {"session": "2026-01-02", "scrip_code": "D", "symbol": "D", "label": 0},
        ]
    )
    selected = _select(frame, pd.Series([0.8, 0.8, 0.1, 0.9]).to_numpy(), 1)
    assert list(selected["scrip_code"]) == ["A", "D"]


def test_executable_replay_is_conservative_when_stop_and_target_share_bar() -> None:
    row = {
        "close": 100.0,
        "atr_14_pct": 0.02,
        "open_1": 100.0,
        "high_1": 103.0,
        "low_1": 97.0,
        "close_1": 101.0,
        "open_2": 101.0,
        "high_2": 102.0,
        "low_2": 100.0,
        "close_2": 101.0,
        "open_3": 101.0,
        "high_3": 102.0,
        "low_3": 100.0,
        "close_3": 101.0,
    }
    bundle = SimpleNamespace(costs=_FreeCosts(), qty_step=1.0, min_notional_inr=0.0)
    replay = _replay_one(row, "nse", bundle)
    assert replay["event"] == "stop"
    assert replay["strict_success"] is False
    assert replay["net_r"] == -1.0


def test_end_to_end_report_is_bound_and_same_dataset_is_not_reopened(
    tmp_path: Path, monkeypatch
) -> None:
    import tradedesk.leader_separability as module

    source = _synthetic_source()
    monkeypatch.setattr(
        module,
        "extract_causal_universe",
        lambda *args, **kwargs: (
            source.copy(),
            {
                "db_path": "synthetic.duckdb",
                "latest_closed_session": "2026-06-29",
                "query_history_start": "2025-01-01",
                "mature_session_start": "2026-01-01",
                "mature_session_end": "2026-06-29",
                "mature_sessions": SESSION_COUNT,
                "raw_rows_considered": len(source),
            },
        ),
    )
    monkeypatch.setattr(module, "_load_replay_paths", lambda db, test: _paths(test))
    monkeypatch.setattr(
        module,
        "_market_bundle",
        lambda market: SimpleNamespace(
            costs=_FreeCosts(), qty_step=1.0, min_notional_inr=0.0
        ),
    )
    output = tmp_path / "m15"
    report = run_leader_separability(
        market="nse",
        db_path=tmp_path / "synthetic.duckdb",
        output_root=output,
        now=datetime(2026, 10, 9, 12, tzinfo=IST),
    )
    validate_separability_report(report)
    assert report["markets_pooled"] is False
    assert report["eligible_for_live"] is False
    assert report["active_model_changed"] is False
    assert len(report["split"]["locked_test"]) == 36
    assert load_latest_separability("nse", output_root=output)["status"] in {
        "development_candidate",
        "rejected_no_separability",
    }

    monkeypatch.setattr(
        module,
        "_build_models",
        lambda: (_ for _ in ()).throw(AssertionError("locked result was reopened")),
    )
    same = run_leader_separability(
        market="nse",
        db_path=tmp_path / "synthetic.duckdb",
        output_root=output,
        now=datetime(2026, 10, 9, 13, tzinfo=IST),
    )
    assert same["experiment_id"] == report["experiment_id"]
