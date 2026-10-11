from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

import tradedesk.pre_takeoff_selector as m19
from tradedesk.broker.indstocks.models import IST
from tradedesk.leader_discovery import FEATURE_COLUMNS


def _features(signal: float) -> dict[str, float]:
    return {
        name: signal if name in {"return_1", "return_5_rank", "volume_ratio_20_rank"} else 0.1
        for name in FEATURE_COLUMNS
    }


def _write_source(path: Path) -> tuple[str, int]:
    rows: list[dict[str, object]] = []
    split_sizes = {"development_train": 42, "calibration": 12, "internal_diagnostic": 12}
    session_number = 0
    for split, sessions in split_sizes.items():
        for _ in range(sessions):
            session_number += 1
            session = f"2026-01-{session_number:03d}"
            for suffix, success, signal in (("A", True, 1.0), ("B", False, -1.0)):
                rows.append(
                    {
                        "version": m19.M18_DATASET_VERSION,
                        "market": "nse",
                        "session": session,
                        "scrip_code": f"{session_number:03d}-{suffix}",
                        "symbol": f"TEST{suffix}",
                        "roles": ["primary"] if suffix == "A" else ["random_01"],
                        "split": split,
                        "features": _features(signal),
                        "precision_label": success,
                        "net_r": 0.75 if success else -1.0,
                        "model_eligible": True,
                    }
                )
    with gzip.open(path, "wt", encoding="utf-8") as target:
        for row in rows:
            target.write(json.dumps(row, sort_keys=True) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest(), len(rows)


def test_top_one_abstains_and_never_emits_two_calls_per_session() -> None:
    scored = pd.DataFrame(
        [
            {"session": "s1", "scrip_code": "a", "probability": 0.8},
            {"session": "s1", "scrip_code": "b", "probability": 0.7},
            {"session": "s2", "scrip_code": "c", "probability": 0.4},
        ]
    )
    selected = m19._top_one(scored, threshold=0.5)
    assert list(selected["scrip_code"]) == ["a"]
    assert selected.groupby("session").size().max() == 1


def test_m19_opens_diagnostic_once_but_does_not_claim_baseline_improvement(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = tmp_path / "m18.jsonl.gz"
    digest, rows = _write_source(dataset)
    readiness = {
        "status": "ready_for_development",
        "experiment_id": "m18-nse",
        "dataset": {"path": str(dataset), "sha256": digest, "rows": rows},
        "blockers": [],
    }
    monkeypatch.setattr(m19, "load_selection_readiness_status", lambda *args, **kwargs: readiness)

    result = m19.run_pre_takeoff_selector(
        "nse",
        output_root=tmp_path / "m19",
        generated_at=datetime(2026, 10, 11, 9, 0, tzinfo=IST),
    )
    assert result["diagnostic_opened"] is True
    assert result["selected_policy"] is not None
    assert result["diagnostic"]["calls"] == 12
    assert result["diagnostic"]["strict_accuracy"] == 1.0
    # Perfect separation is still rejected when it does not improve over forced top-one.
    assert result["status"] == "development_rejected"
    assert result["gates"]["accuracy_advantage_at_least_10pp"] is False
    assert result["baseline_accuracy_improved"] is False
    assert result["eligible_for_live"] is False

    repeated = m19.run_pre_takeoff_selector(
        "nse", output_root=tmp_path / "m19", generated_at=datetime.now(IST)
    )
    assert repeated["artifact_sha256"] == result["artifact_sha256"]


def test_bse_failure_is_unavailable_not_a_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        m19,
        "load_selection_readiness_status",
        lambda *args, **kwargs: {
            "status": "not_ready",
            "blockers": ["development_train:coverage=0.50 (requires 0.85)"],
        },
    )
    result = m19.run_pre_takeoff_selector("bse", output_root=tmp_path)
    assert result["status"] == "not_ready"
    assert result["diagnostic_opened"] is False
    assert result["baseline_accuracy_improved"] is False
    assert result["eligible_for_live"] is False
