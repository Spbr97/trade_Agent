from __future__ import annotations

from tests.unit.test_outcome_resolver import _bars, _row
from tradedesk.prediction_ledger import seal_prediction
from tradedesk.self_learning_workflow import refresh_learning_status, run_scheduled_challenger
from tradedesk.signal_tracker import resolve_outcomes


def _resolved(i: int, version: str = "quick-profit-v1"):  # type: ignore[no-untyped-def]
    row = _row(version=version)
    signal_id = f"sealed-{version}-{i}"
    payload = dict(row.prediction_payload or {})
    payload["signal_id"] = signal_id
    row.signal_id = signal_id
    row.prediction_payload = payload
    row.prediction_sha256 = seal_prediction(payload)

    class Store:
        def load(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            high = 111 if version == "swing-v1" else 104
            return _bars([(99, high, 98, 103)])

    resolve_outcomes(Store(), {signal_id: row}, 99)  # type: ignore[arg-type]
    return row


def test_session_refresh_waits_honestly_and_never_changes_active_model(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "status.json"
    report = refresh_learning_status("nse", {}, path)
    assert report["status"] == "waiting_for_sealed_evidence"
    assert report["eligible_mature"] == 0
    assert report["remaining_for_challenger"] == 20
    assert not report["active_model_changed"]
    assert not report["promotion_authorized"]
    assert path.exists()


def test_enough_new_mature_evidence_becomes_ready_but_does_not_train(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rows = {f"sealed-{i}": _resolved(i) for i in range(20)}
    path = tmp_path / "status.json"
    first = refresh_learning_status("nse", rows, path)
    second = refresh_learning_status("nse", rows, path)
    assert first["status"] == second["status"] == "ready_for_weekly_challenger"
    assert first["new_mature_since_last_refresh"] == 20
    assert second["new_mature_since_last_refresh"] == 20
    assert not second["active_model_changed"]
    assert second["last_challenger_dataset_id"] is None


def test_scheduled_challenger_refuses_one_session_and_does_not_consume_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rows = {f"sealed-{i}": _resolved(i) for i in range(20)}
    path = tmp_path / "status.json"
    report = run_scheduled_challenger(
        "nse", rows, path, output_root=tmp_path / "models"
    )
    assert report["status"] == "challenger_failed_or_blocked"
    assert report["challenger_consumed_signal_ids"] == []
    assert "independent sessions" in " ".join(report["blockers"])
    assert report["active_model_changed"] is False
    assert report["promotion_authorized"] is False


def test_quick_and_swing_counts_cannot_combine_to_trigger_training(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rows = {
        row.signal_id: row
        for version in ("quick-profit-v1", "swing-v1")
        for i in range(10)
        if (row := _resolved(i, version))
    }
    report = refresh_learning_status("nse", rows, tmp_path / "status.json")
    assert report["status"] == "waiting_for_sealed_evidence"
    assert report["eligible_mature"] == 20
    assert report["contract_status"]["quick-profit-v1"]["new_mature"] == 10
    assert report["contract_status"]["swing-v1"]["new_mature"] == 10
