from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.crypto_accuracy_recovery import (  # noqa: E402
    run_crypto_accuracy_recovery,
)


def main() -> None:
    report = run_crypto_accuracy_recovery()
    best = report["best_diagnostic"]
    nominee = best.get("nominee") or {}
    walk_forward = best.get("walk_forward") or {}
    live = best.get("live_validation") or {}
    print(
        "Crypto R3 accuracy recovery: "
        f"status={report['status']}, trials={report['trial_counts']['registered_total']}, "
        f"best={best.get('setup')}:{nominee.get('candidate_id')}, "
        f"walk_forward={walk_forward.get('observed_accuracy')}, "
        f"live_validation={live.get('observed_accuracy')}, "
        "baseline_improved=false, eligible_for_live=false"
    )


if __name__ == "__main__":
    main()
