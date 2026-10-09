from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import IST
from tradedesk.execution_aligned_selector import (
    OBSERVATION_VERSION,
    SESSION_COUNT,
    _append_chain,
    _candidate_pool,
    _load_chain,
    _select_policy,
    collect_execution_aligned_prospective,
    load_development_report,
    run_execution_aligned_development,
    validate_development_report,
)
from tradedesk.leader_discovery import FEATURE_COLUMNS, _prepare_causal_frame, spec_for


def _scored_frame(session_rows: tuple[int, ...] = (250, 2_500)) -> pd.DataFrame:
    rows = []
    for session_index, count in enumerate(session_rows):
        session = f"2026-01-{session_index + 1:02d}"
        for index in range(count):
            rows.append(
                {
                    "session": session,
                    "scrip_code": f"NSE_{index:05d}",
                    "symbol": f"S{index}",
                    "opportunity_score": 1.0 - index / max(count, 1),
                    "trade_probability": 0.9 if index == 1 else 0.4,
                    "predicted_net_r": 0.2,
                    "strict_success": index == 1,
                    "net_r": 0.6 if index == 1 else -1.0,
                    "event": "target" if index == 1 else "stop",
                }
            )
    return pd.DataFrame(rows)


def _synthetic_source() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = date(2026, 1, 1)
    for session_index in range(SESSION_COUNT):
        session = start + timedelta(days=session_index)
        for instrument in range(12):
            row: dict[str, object] = {
                "session_date": session,
                "scrip_code": f"NSE_{instrument}",
                "symbol": f"S{instrument}",
                "opportunity_label": instrument == 0,
            }
            for feature_index, feature in enumerate(FEATURE_COLUMNS):
                row[feature] = (
                    (2.0 if instrument == 0 else 1.8 if instrument == 1 else 0.1)
                    + feature_index / 1_000
                    + session_index / 100_000
                )
            rows.append(row)
    return pd.DataFrame(rows)


def test_candidate_pool_is_bounded_and_policy_can_abstain() -> None:
    scored = _scored_frame()
    pool = _candidate_pool(scored)
    counts = pool.groupby("session").size().to_dict()
    assert counts == {"2026-01-01": 3, "2026-01-02": 20}

    selected = _select_policy(scored, 0.80)
    assert list(selected["scrip_code"]) == ["NSE_00001", "NSE_00001"]
    assert _select_policy(scored, 0.95).empty


def test_hash_chain_rejects_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "observations.jsonl"
    cohort = "c" * 64
    first = _append_chain(
        path,
        {
            "version": OBSERVATION_VERSION,
            "market": "nse",
            "cohort_id": cohort,
            "session": "2026-10-10",
        },
        [],
    )
    _append_chain(
        path,
        {
            "version": OBSERVATION_VERSION,
            "market": "nse",
            "cohort_id": cohort,
            "session": "2026-10-11",
        },
        [first],
    )
    assert len(
        _load_chain(
            path,
            market="nse",
            cohort_id=cohort,
            version=OBSERVATION_VERSION,
        )
    ) == 2

    rows = path.read_text(encoding="utf-8").splitlines()
    changed = json.loads(rows[0])
    changed["session"] = "2026-10-09"
    rows[0] = json.dumps(changed, sort_keys=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="chain integrity"):
        _load_chain(
            path,
            market="nse",
            cohort_id=cohort,
            version=OBSERVATION_VERSION,
        )


def test_latest_decision_features_do_not_require_future_bars() -> None:
    row = {
        "session_date": date(2026, 10, 9),
        "scrip_code": "NSE_1",
        "symbol": "ONE",
        "history_sessions": 100,
        "close": 100.0,
        "open": 99.0,
        "high": 102.0,
        "low": 98.0,
        "volume": 1_000_000.0,
        "close_lag_1": 99.0,
        "close_lag_3": 97.0,
        "close_lag_5": 95.0,
        "close_lag_20": 90.0,
        "avg_volume_20": 900_000.0,
        "avg_turnover_20": 100_000_000.0,
        "sma_20": 96.0,
        "sma_50": 92.0,
        "prior_high_20": 101.0,
        "atr_14": 2.0,
        "high_forward_3": np.nan,
        "low_forward_3": np.nan,
        "close_lead_3": np.nan,
    }
    prepared = _prepare_causal_frame(
        pd.DataFrame([row]), spec_for("nse"), require_mature_outcomes=False
    )
    assert len(prepared) == 1
    assert set(FEATURE_COLUMNS).issubset(prepared.columns)
    with pytest.raises(ValueError, match="no mature liquid rows"):
        _prepare_causal_frame(
            pd.DataFrame([row]), spec_for("nse"), require_mature_outcomes=True
        )


def test_development_report_is_bound_fail_closed_and_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    import tradedesk.execution_aligned_selector as module

    source = _synthetic_source()
    monkeypatch.setattr(
        module,
        "extract_causal_universe",
        lambda *args, **kwargs: (
            source.copy(),
            {
                "db_path": "synthetic.duckdb",
                "latest_closed_session": "2026-10-09",
                "query_history_start": "2025-01-01",
                "mature_session_start": "2026-01-01",
                "mature_session_end": "2026-06-29",
                "mature_sessions": SESSION_COUNT,
                "raw_rows_considered": len(source),
            },
        ),
    )

    def execution(extracted: pd.DataFrame, db: Path, market: str):
        frame = module._model_frame(extracted)
        winning = frame["scrip_code"] == "NSE_1"
        frame["status"] = "resolved"
        frame["event"] = np.where(winning, "target", "stop")
        frame["strict_success"] = winning
        frame["net_r"] = np.where(winning, 0.6, -1.1)
        return frame, []

    monkeypatch.setattr(module, "_execution_frame", execution)
    output = tmp_path / "m16"
    report = run_execution_aligned_development(
        market="nse",
        db_path=tmp_path / "synthetic.duckdb",
        output_root=output,
        now=datetime(2026, 10, 9, 18, tzinfo=IST),
    )
    validate_development_report(report)
    assert report["eligible_for_live"] is False
    assert report["active_model_changed"] is False
    assert report["baseline_accuracy_improved"] is False
    assert report["split"]["locked_test"] is None
    assert len(report["split"]["consumed_diagnostic"]) == 36
    assert load_development_report("nse", output_root=output)["status"] in {
        "prospective_registered",
        "development_rejected",
    }

    monkeypatch.setattr(
        module,
        "extract_causal_universe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing M16 experiment was reopened")
        ),
    )
    same = run_execution_aligned_development(
        market="nse",
        db_path=tmp_path / "synthetic.duckdb",
        output_root=output,
        now=datetime(2026, 10, 9, 19, tzinfo=IST),
    )
    assert same["experiment_id"] == report["experiment_id"]


def test_prospective_collector_starts_strictly_after_registration_and_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    import tradedesk.execution_aligned_selector as module

    registration = {
        "cohort_id": "c" * 64,
        "experiment_id": "e" * 64,
        "starts_strictly_after": "2026-10-09",
        "threshold": 0.50,
        "model_sha256": "m" * 64,
    }
    session = {"value": date(2026, 10, 9)}

    def decision(*args, **kwargs):
        rows = []
        for index in range(4):
            row: dict[str, object] = {
                "session_date": session["value"],
                "scrip_code": f"NSE_{index}",
                "symbol": f"S{index}",
                "close": 100.0 + index,
            }
            row.update({name: float(index + 1) / 10 for name in FEATURE_COLUMNS})
            rows.append(row)
        value = session["value"].isoformat()
        return pd.DataFrame(rows), {
            "decision_session": value,
            "latest_closed_session": value,
            "universe_rows": len(rows),
        }

    def score(frame: pd.DataFrame, models):
        result = frame.copy()
        result["opportunity_score"] = [0.9, 0.8, 0.7, 0.6]
        result["opportunity_score_rank"] = [1.0, 0.75, 0.5, 0.25]
        result["trade_probability"] = [0.9, 0.4, 0.3, 0.2]
        result["predicted_net_r"] = [0.2, 0.1, 0.1, 0.1]
        return result

    monkeypatch.setattr(
        module,
        "load_development_report",
        lambda *args, **kwargs: {"status": "prospective_registered"},
    )
    monkeypatch.setattr(
        module, "_load_registration", lambda *args, **kwargs: (registration, Path("x"))
    )
    monkeypatch.setattr(module, "_resolve_pending", lambda **kwargs: [])
    monkeypatch.setattr(module, "extract_decision_universe", decision)
    monkeypatch.setattr(module, "_load_prospective_model", lambda value: {"models": {}})
    monkeypatch.setattr(module, "_score_pipeline", score)

    output = tmp_path / "m16"
    before = collect_execution_aligned_prospective(
        market="nse", db_path=tmp_path / "db", output_root=output
    )
    assert before["observed_sessions"] == 0

    session["value"] = date(2026, 10, 10)
    first = collect_execution_aligned_prospective(
        market="nse", db_path=tmp_path / "db", output_root=output
    )
    second = collect_execution_aligned_prospective(
        market="nse", db_path=tmp_path / "db", output_root=output
    )
    assert first["observed_sessions"] == 1
    assert first["selected_calls"] == 1
    assert second["observed_sessions"] == 1
