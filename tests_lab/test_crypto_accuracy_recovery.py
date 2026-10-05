from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest
import tradedesk_lab.crypto_accuracy_recovery as recovery
from tradedesk_lab.crypto_accuracy_recovery import (
    RecoveryCandidate,
    RecoveryContract,
    run_crypto_accuracy_recovery,
    verify_crypto_accuracy_recovery,
)


class ZeroCost:
    slippage_pct = 0.0

    def round_trip_cost(self, **_kwargs):
        return SimpleNamespace(total=0.0)


def _bars(*rows: tuple[float, float, float, float]) -> pd.DataFrame:
    index = pd.date_range("2026-01-02", periods=len(rows), freq="D", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index).assign(
        volume=1000
    )


def _replay_frame(
    *,
    backfill: int = 10,
    live: int = 3,
    backfill_wins: int = 8,
    live_wins: int = 2,
) -> pd.DataFrame:
    rows = []
    for source, count, wins, start in (
        ("backfill", backfill, backfill_wins, "2026-01-01"),
        ("live", live, live_wins, "2026-03-01"),
    ):
        sessions = pd.date_range(start, periods=count, freq="D")
        for index, session in enumerate(sessions):
            won = index < wins
            rows.append(
                {
                    "signal_id": f"{source}-{index}",
                    "scrip_code": "CDX_TESTINR",
                    "symbol": "TESTINR",
                    "setup": "nr7_breakout",
                    "armed_on": session.date().isoformat(),
                    "source": source,
                    "shadow": False,
                    "candidate_id": "target_0p5r_hold_1d",
                    "target_r": 0.5,
                    "max_hold_sessions": 1,
                    "status": "resolved",
                    "exclusion_reason": None,
                    "outcome": "target" if won else "stop",
                    "label": int(won),
                    "gross_r": 0.5 if won else -1.0,
                    "net_r": 0.4 if won else -1.1,
                    "after_tax_r": 0.3 if won else -1.1,
                }
            )
    return pd.DataFrame(rows)


def _small_contract() -> RecoveryContract:
    return RecoveryContract(
        target_rs=(0.5,),
        max_hold_sessions=(1,),
        folds=2,
        initial_train_fraction=0.4,
        embargo_days=1,
        minimum_development_resolved=1,
        minimum_walk_forward_resolved=1,
        minimum_live_validation_resolved=1,
        minimum_accuracy=0.5,
        minimum_wilson95_lower=0.0,
        minimum_mean_net_r=-1.0,
        minimum_positive_folds=1,
    )


def test_contract_embargo_covers_longest_label_window() -> None:
    with pytest.raises(ValueError, match="embargo"):
        RecoveryContract(max_hold_sessions=(1, 7), embargo_days=6)


def test_replay_is_stop_first_when_one_bar_touches_both_barriers() -> None:
    row = {
        "signal_id": "nr7:TEST:2026-01-01",
        "scrip_code": "CDX_TESTINR",
        "symbol": "TESTINR",
        "setup": "nr7_breakout",
        "armed_on": "2026-01-01",
        "entry": 100.0,
        "stop": 90.0,
        "source": "backfill",
    }

    result = recovery._replay_one(
        row,
        _bars((100.0, 110.0, 85.0, 105.0)),
        RecoveryCandidate(target_r=0.5, max_hold_sessions=1),
        costs=ZeroCost(),
        tds_share=0.0,
    )

    assert result["status"] == "resolved"
    assert result["outcome"] == "stop"
    assert result["label"] == 0
    assert result["net_r"] == pytest.approx(-1.0)


def test_candidate_ranking_does_not_prefer_accuracy_with_negative_expectancy() -> None:
    rows = []
    for candidate, wins, net in (("tiny", 9, -0.1), ("sound", 7, 0.2)):
        for index in range(10):
            rows.append(
                {
                    "candidate_id": candidate,
                    "target_r": 0.25 if candidate == "tiny" else 0.75,
                    "max_hold_sessions": 1,
                    "status": "resolved",
                    "armed_on": f"2026-01-{index + 1:02d}",
                    "label": int(index < wins),
                    "gross_r": net,
                    "net_r": net,
                    "after_tax_r": net,
                }
            )

    ranked = recovery._candidate_table(pd.DataFrame(rows))

    assert ranked[0]["candidate_id"] == "sound"
    assert ranked[1]["metrics"]["observed_accuracy"] == pytest.approx(0.9)


def test_setup_report_keeps_backfill_selection_and_live_validation_separate() -> None:
    report = recovery._setup_report(
        _replay_frame(), "nr7_breakout", _small_contract()
    )

    assert report["development"]["resolved_calls"] == 10
    assert report["live_validation"]["resolved_calls"] == 3
    assert report["nominee"]["candidate_id"] == "target_0p5r_hold_1d"
    assert report["walk_forward"]["folds"]
    assert all(
        fold["train_through"] < fold["test_first_session"]
        for fold in report["walk_forward"]["folds"]
    )


def test_recovery_report_is_research_only_even_when_small_contract_passes(
    tmp_path, monkeypatch
) -> None:
    log_path = tmp_path / "tracker.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "signal_id": "one",
                "scrip_code": "CDX_TESTINR",
                "setup": "nr7_breakout",
                "armed_on": "2026-01-01",
                "entry": 100.0,
                "stop": 90.0,
                "source": "backfill",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(recovery, "build_replay", lambda *_args, **_kwargs: _replay_frame())

    report = run_crypto_accuracy_recovery(
        log_path=log_path,
        db_path=tmp_path / "unused.duckdb",
        output=tmp_path / "output",
        contract=_small_contract(),
    )

    assert report["status"] == "shadow_candidate_ready_research_only"
    assert report["research_gate_passed"] is True
    assert report["baseline_improved"] is False
    assert report["eligible_for_live"] is False
    assert report["source"]["backfill_rows"] == 1
    assert (tmp_path / "output" / "state.json").exists()
    assert (tmp_path / "output" / "runs" / report["id"] / "manifest.json").exists()
    assert verify_crypto_accuracy_recovery(tmp_path / "output") == report

    state_path = tmp_path / "output" / "state.json"
    tampered = json.loads(state_path.read_text(encoding="utf-8"))
    tampered["eligible_for_live"] = True
    state_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="failed verification"):
        verify_crypto_accuracy_recovery(tmp_path / "output")


def test_duplicate_tracker_identity_fails_closed(tmp_path) -> None:
    path = tmp_path / "tracker.jsonl"
    row = {
        "signal_id": "duplicate",
        "scrip_code": "CDX_TESTINR",
        "setup": "nr7_breakout",
        "armed_on": "2026-01-01",
        "entry": 100.0,
        "stop": 90.0,
    }
    path.write_text("\n".join((json.dumps(row), json.dumps(row))), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate signal IDs"):
        recovery._read_tracker(path)


def test_global_diagnostic_ranking_does_not_use_live_validation(
    tmp_path, monkeypatch
) -> None:
    log_path = tmp_path / "tracker.jsonl"
    log_path.write_text(
        "\n".join(
            json.dumps(
                {
                    "signal_id": setup,
                    "scrip_code": f"CDX_{setup.upper()}INR",
                    "setup": setup,
                    "armed_on": "2026-01-01",
                    "entry": 100.0,
                    "stop": 90.0,
                }
            )
            for setup in ("better_oos", "better_live")
        ),
        encoding="utf-8",
    )
    replay = _replay_frame(backfill=2, live=0, backfill_wins=1)
    replay = pd.concat(
        [replay.assign(setup="better_oos"), replay.assign(setup="better_live")],
        ignore_index=True,
    )
    monkeypatch.setattr(recovery, "build_replay", lambda *_args, **_kwargs: replay)

    def report(_replay, setup, _contract):
        better_oos = setup == "better_oos"

        def metrics(accuracy, net):
            return {
                "resolved_calls": 10,
                "wins": int(accuracy * 10),
                "active_sessions": 10,
                "observed_accuracy": accuracy,
                "wilson95_lower": accuracy / 2,
                "mean_gross_r": net,
                "mean_net_r": net,
                "mean_after_tax_r": net,
            }

        return {
            "setup": setup,
            "status": "rejected_or_insufficient",
            "nominee": {
                "candidate_id": "target_0p5r_hold_1d",
                "target_r": 0.5,
                "max_hold_sessions": 1,
            },
            "development": metrics(0.7 if better_oos else 0.6, 0.2),
            "walk_forward": {
                **metrics(0.7 if better_oos else 0.6, 0.2),
                "folds": [],
                "positive_folds": 0,
            },
            "live_validation": metrics(0.1 if better_oos else 0.9, 0.2),
            "gates": {},
            "passed_research_gate": False,
            "trials": [],
        }

    monkeypatch.setattr(recovery, "_setup_report", report)

    result = run_crypto_accuracy_recovery(
        log_path=log_path,
        db_path=tmp_path / "unused.duckdb",
        output=tmp_path / "output",
        contract=_small_contract(),
    )

    assert result["best_diagnostic"]["setup"] == "better_oos"
