from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
import tradedesk_lab.accuracy_collection_health as health_module
from tradedesk_lab.accuracy_collection_health import (
    PipelineStep,
    pipeline_steps,
    run_collection_pipeline,
    verify_collection_health,
)


def test_registered_bse_pipeline_enforces_dependency_order() -> None:
    steps = pipeline_steps("bse", python="python")

    assert [step.name for step in steps] == [
        "data_load",
        "scan",
        "signal_tracker",
        "research_and_b1",
    ]
    assert steps[-1].argv[-1] == "--strict-accuracy-refresh"
    assert steps[2].argv[-1] == "--strict-accuracy-refresh"


def test_registered_nse_pipeline_keeps_m8_m11_after_scan() -> None:
    names = [step.name for step in pipeline_steps("nse", python="python")]

    assert names.index("scan") < names.index("signal_and_m8_m11")
    assert names.index("signal_and_m8_m11") < names.index("research_tracker")


def test_registered_crypto_pipeline_runs_recovery_after_c1_c2_as_optional() -> None:
    steps = pipeline_steps("crypto", python="python")

    assert [step.name for step in steps] == [
        "data_load",
        "signal_c1_c2",
        "accuracy_recovery_r3",
    ]
    assert steps[-1].critical is False


def test_critical_failure_stops_dependent_evidence_and_is_hash_bound(tmp_path) -> None:
    calls: list[str] = []

    def runner(argv, **_kwargs):
        calls.append(argv[-1])
        return subprocess.CompletedProcess(
            argv,
            1 if argv[-1] == "scan" else 0,
            stdout="scan failed" if argv[-1] == "scan" else "ok",
            stderr="",
        )

    steps = (
        PipelineStep("data", ("python", "data")),
        PipelineStep("scan", ("python", "scan")),
        PipelineStep("evidence", ("python", "evidence")),
    )
    output = tmp_path / "health"
    report = run_collection_pipeline(
        "nse", root=tmp_path, output=output, steps=steps, runner=runner
    )

    assert calls == ["data", "scan"]
    assert report["status"] == "failed"
    assert report["failed_stage"] == "scan"
    assert report["stages"][-1]["status"] == "skipped_dependency_failure"
    assert report["baseline_improved"] is False
    assert report["eligible_for_live"] is False
    verification = verify_collection_health("nse", output=output)
    assert verification["passed"] is False
    assert verification["integrity_passed"] is True
    assert verification["freshness_passed"] is True
    assert verification["operationally_completed"] is False


def test_noncritical_failure_is_visible_but_does_not_stop_collection(tmp_path) -> None:
    def runner(argv, **_kwargs):
        code = 1 if argv[-1] == "optional" else 0
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="optional failed")

    steps = (
        PipelineStep("optional", ("python", "optional"), critical=False),
        PipelineStep("evidence", ("python", "evidence")),
    )
    report = run_collection_pipeline(
        "bse", root=tmp_path, output=tmp_path / "health", steps=steps, runner=runner
    )

    assert report["status"] == "degraded"
    assert [stage["status"] for stage in report["stages"]] == ["failed", "completed"]
    verification = verify_collection_health("bse", output=tmp_path / "health")
    assert verification["passed"] is False
    assert verification["integrity_passed"] is True
    assert verification["operationally_completed"] is False


def test_final_receipt_stays_inside_lock_and_queued_run_cannot_be_overwritten(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "health"
    steps = (PipelineStep("collect", ("python", "collect")),)
    original_write_state = health_module._write_state
    first_run_id: list[str] = []
    first_final_waiting = threading.Event()
    allow_first_final = threading.Event()
    second_running_written = threading.Event()
    reports: list[dict] = []
    errors: list[BaseException] = []

    def guarded_write_state(output_path, market, report, *, final):
        run_id = str(report["run_id"])
        if not first_run_id:
            first_run_id.append(run_id)
        if final and run_id == first_run_id[0]:
            first_final_waiting.set()
            assert allow_first_final.wait(timeout=3)
        elif not final and run_id != first_run_id[0]:
            second_running_written.set()
        original_write_state(output_path, market, report, final=final)

    monkeypatch.setattr(health_module, "_write_state", guarded_write_state)

    def collect() -> None:
        try:
            reports.append(
                run_collection_pipeline(
                    "crypto",
                    root=tmp_path,
                    output=output,
                    steps=steps,
                    runner=lambda argv, **_kwargs: subprocess.CompletedProcess(
                        argv, 0, "ok", ""
                    ),
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    first = threading.Thread(target=collect)
    first.start()
    assert first_final_waiting.wait(timeout=3)

    second = threading.Thread(target=collect)
    second.start()
    overlapped_before_final = second_running_written.wait(timeout=0.25)
    allow_first_final.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert overlapped_before_final is False
    assert first.is_alive() is False
    assert second.is_alive() is False
    assert errors == []
    assert len(reports) == 2
    assert second_running_written.is_set()
    latest = json.loads((output / "crypto.json").read_text(encoding="utf-8"))
    assert latest["run_id"] == reports[-1]["run_id"]
    assert len(list((output / "runs" / "crypto").glob("*.json"))) == 2


def test_tampered_collection_health_fails_closed(tmp_path) -> None:
    output = tmp_path / "health"
    report = run_collection_pipeline(
        "crypto",
        root=tmp_path,
        output=output,
        steps=(PipelineStep("collect", ("python", "collect")),),
        runner=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""),
    )
    path = output / "crypto.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["status"] = "failed" if report["status"] == "completed" else "completed"
    path.write_text(json.dumps(stored), encoding="utf-8")

    verification = verify_collection_health("crypto", output=output)

    assert verification["passed"] is False
    assert "report_sha256" in verification["errors"]


def test_stale_collection_health_is_not_available_even_when_hash_is_valid(tmp_path) -> None:
    output = tmp_path / "health"
    run_collection_pipeline(
        "crypto",
        root=tmp_path,
        output=output,
        steps=(PipelineStep("collect", ("python", "collect")),),
        runner=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""),
    )

    verification = verify_collection_health(
        "crypto", output=output, now=datetime.now(UTC) + timedelta(hours=37)
    )

    assert verification["passed"] is False
    assert verification["integrity_passed"] is True
    assert verification["freshness_passed"] is False
    assert "stale" in verification["errors"]


def test_strict_equity_trackers_fail_when_their_watchlist_is_missing(
    tmp_path, monkeypatch
) -> None:
    import scripts.bse_signal_tracker as bse_tracker
    import scripts.nse_signal_tracker as nse_tracker

    monkeypatch.setattr(nse_tracker, "WATCHLIST_DIR", tmp_path / "nse")
    monkeypatch.setattr(bse_tracker, "WATCHLIST_DIR", tmp_path / "bse")

    with pytest.raises(RuntimeError, match="no watchlist"):
        nse_tracker.main(strict_accuracy_refresh=True)
    with pytest.raises(RuntimeError, match="no watchlist"):
        bse_tracker.main(strict_accuracy_refresh=True)


def test_strict_nse_tracker_propagates_degraded_m9(tmp_path, monkeypatch) -> None:
    import scripts.nse_signal_tracker as tracker
    import tradedesk_lab.accuracy_prospective_control as control_module
    import tradedesk_lab.accuracy_prospective_monitor as monitor_module
    import tradedesk_lab.accuracy_prospective_qualification as qualification_module
    import tradedesk_lab.accuracy_prospective_shadow as shadow_module
    import tradedesk_lab.accuracy_prospective_timing as timing_module

    watchlists = tmp_path / "watchlists"
    watchlists.mkdir()
    (watchlists / "2026-10-05.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tracker, "WATCHLIST_DIR", watchlists)
    monkeypatch.setattr(
        tracker,
        "load_watchlist",
        lambda _path: SimpleNamespace(on=date(2026, 10, 5), entries=[], active=[]),
    )

    class Store:
        def __init__(self, _path) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(tracker, "CandleStore", Store)
    monkeypatch.setattr(tracker, "load_log", lambda _path: {})
    monkeypatch.setattr(tracker, "log_new_signals", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(tracker, "resolve_outcomes", lambda *_args: [])
    monkeypatch.setattr(tracker, "save_log", lambda *_args: None)
    monkeypatch.setattr(tracker, "refresh_learning_status", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tracker, "save_dashboard", lambda *_args: None)
    monkeypatch.setattr(tracker, "render_session_report", lambda *_args: "report")
    monkeypatch.setattr(tracker, "save_session_report", lambda *_args: tmp_path / "report")
    monkeypatch.setattr(tracker, "flag_setup_failures", lambda *_args: [])
    monkeypatch.setattr(tracker, "scoreboard", lambda *_args: "scoreboard")
    monkeypatch.setattr(
        shadow_module,
        "collect",
        lambda: {
            "summary": {"selected_calls": 0, "resolved_calls": 0, "status": "collecting"},
            "current_errors": [],
        },
    )
    monkeypatch.setattr(
        monitor_module,
        "run_monitor",
        lambda: {
            "integrity": {
                "status": "degraded",
                "passed": False,
                "failures": ["missing_watchlist_session"],
            },
            "audit": {"events": 0},
            "review_ready": False,
            "double_slippage_stress": {"status": "insufficient_evidence"},
        },
    )
    monkeypatch.setattr(
        control_module,
        "collect_control",
        lambda: {
            "summary": {
                "resolved_model_calls": 0,
                "mature_sessions": 0,
                "status": "collecting_insufficient_evidence",
            }
        },
    )
    monkeypatch.setattr(
        timing_module,
        "collect_timing",
        lambda: {
            "summary": {
                "paired_resolved_calls": 0,
                "active_sessions": 0,
                "status": "collecting_insufficient_evidence",
            }
        },
    )
    monkeypatch.setattr(
        qualification_module,
        "run_qualification",
        lambda: {
            "status": "collecting_insufficient_evidence",
            "review_authorized": False,
            "eligible_for_live": False,
        },
    )

    with pytest.raises(RuntimeError, match="M9 integrity: missing_watchlist_session"):
        tracker.main(strict_accuracy_refresh=True)
