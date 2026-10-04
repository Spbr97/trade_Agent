from __future__ import annotations

import pytest
from tradedesk_lab.crypto_accuracy_pipeline import refresh_crypto_accuracy


def test_refresh_runs_c1_before_c2_and_returns_compact_result() -> None:
    calls: list[str] = []

    def materialize() -> dict[str, object]:
        calls.append("c1")
        return {
            "id": "crypto-accuracy-dataset-c1",
            "status": "not_ready_collecting_point_in_time_history",
            "source_integrity": {"passed": True, "errors": []},
            "membership": {"registered_pair_count": 337},
        }

    def evaluate() -> dict[str, object]:
        calls.append("c2")
        return {
            "id": "crypto-accuracy-mechanisms-c2",
            "status": "not_ready",
            "trial_counts": {"registered": 12, "evaluated": 0},
            "large_unneeded_payload": [1, 2, 3],
        }

    result = refresh_crypto_accuracy(
        materialize_dataset=materialize,
        run_mechanisms=evaluate,
    )

    assert calls == ["c1", "c2"]
    assert result == {
        "status": "completed",
        "c1": {
            "id": "crypto-accuracy-dataset-c1",
            "status": "not_ready_collecting_point_in_time_history",
        },
        "c2": {
            "id": "crypto-accuracy-mechanisms-c2",
            "status": "not_ready",
            "trial_counts": {"registered": 12, "evaluated": 0},
        },
        "baseline_improved": False,
        "eligible_for_live": False,
    }


@pytest.mark.parametrize(
    "source_integrity, message",
    [
        (None, "source integrity result is missing"),
        (
            {"passed": False, "errors": ["future observation"]},
            "source integrity check failed",
        ),
        ({"errors": []}, "source integrity check failed"),
    ],
)
def test_refresh_fails_closed_and_skips_c2(
    source_integrity: object,
    message: str,
) -> None:
    c2_called = False

    def materialize() -> dict[str, object]:
        result: dict[str, object] = {"id": "c1", "status": "not_ready"}
        if source_integrity is not None:
            result["source_integrity"] = source_integrity
        return result

    def evaluate() -> dict[str, object]:
        nonlocal c2_called
        c2_called = True
        return {"id": "c2", "status": "not_ready"}

    with pytest.raises(ValueError, match=message):
        refresh_crypto_accuracy(
            materialize_dataset=materialize,
            run_mechanisms=evaluate,
        )

    assert c2_called is False


def test_refresh_skips_c2_when_c1_raises() -> None:
    c2_called = False

    def materialize() -> dict[str, object]:
        raise RuntimeError("materialization failed")

    def evaluate() -> dict[str, object]:
        nonlocal c2_called
        c2_called = True
        return {}

    with pytest.raises(RuntimeError, match="materialization failed"):
        refresh_crypto_accuracy(
            materialize_dataset=materialize,
            run_mechanisms=evaluate,
        )

    assert c2_called is False


def test_refresh_rejects_non_mapping_stage_results() -> None:
    with pytest.raises(TypeError, match="C1 materializer"):
        refresh_crypto_accuracy(
            materialize_dataset=lambda: None,  # type: ignore[return-value]
            run_mechanisms=lambda: {},
        )

    with pytest.raises(TypeError, match="C2 evaluator"):
        refresh_crypto_accuracy(
            materialize_dataset=lambda: {
                "source_integrity": {"passed": True, "errors": []}
            },
            run_mechanisms=lambda: None,  # type: ignore[return-value]
        )
