"""Daily orchestration for the crypto C1 -> C2 evidence refresh.

The signal tracker owns collection of the point-in-time universe observation.
This module only refreshes the derived C1 and C2 artifacts after collection; it
does not relax either checkpoint's readiness or promotion contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tradedesk_lab.crypto_accuracy_dataset import materialize_crypto_dataset
from tradedesk_lab.crypto_accuracy_mechanisms import run_crypto_accuracy_mechanisms

Checkpoint = Mapping[str, Any]
CheckpointRunner = Callable[..., dict[str, Any]]


def _require_c1_source_integrity(checkpoint: Checkpoint) -> None:
    """Fail closed unless C1 explicitly reports a clean source audit."""

    source_integrity = checkpoint.get("source_integrity")
    if not isinstance(source_integrity, Mapping):
        raise ValueError("crypto C1 source integrity result is missing")
    if source_integrity.get("passed") is not True:
        errors = source_integrity.get("errors")
        detail = f": {errors}" if errors else ""
        raise ValueError(f"crypto C1 source integrity check failed{detail}")


def refresh_crypto_accuracy(
    *,
    materialize_dataset: CheckpointRunner | None = None,
    run_mechanisms: CheckpointRunner | None = None,
) -> dict[str, Any]:
    """Refresh C1 and, only after its integrity gate passes, refresh C2.

    Dependencies are injectable so the scheduler behavior can be verified
    without writing checkpoint artifacts or calling data providers.
    """

    materialize = materialize_dataset or materialize_crypto_dataset
    evaluate = run_mechanisms or run_crypto_accuracy_mechanisms

    c1 = materialize()
    if not isinstance(c1, Mapping):
        raise TypeError("crypto C1 materializer returned a non-mapping result")
    _require_c1_source_integrity(c1)

    c2 = evaluate()
    if not isinstance(c2, Mapping):
        raise TypeError("crypto C2 evaluator returned a non-mapping result")

    return {
        "status": "completed",
        "c1": {
            "id": c1.get("id"),
            "status": c1.get("status"),
        },
        "c2": {
            "id": c2.get("id"),
            "status": c2.get("status"),
            "trial_counts": c2.get("trial_counts", {}),
        },
        # This is an evidence-maintenance job, never a promotion authority.
        "baseline_improved": False,
        "eligible_for_live": False,
    }


__all__ = ["refresh_crypto_accuracy"]
