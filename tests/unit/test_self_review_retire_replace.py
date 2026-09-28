"""self_review/retire_replace.py: retirement only follows SUSTAINED failure, and only
proposes a replacement if the lab registry already has one under the exact family."""

from __future__ import annotations

from pathlib import Path

from tradedesk_lab.registry import Registry

from tradedesk.engine.signals import SetupKind
from tradedesk.rolling_failure_monitor import FailureSeverity, SustainedFailure
from tradedesk.self_review import retire_replace as rr


def _failure(severity: FailureSeverity) -> SustainedFailure:
    return SustainedFailure(
        market="nse", setup="nr7_breakout", severity=severity, as_of_date="2026-09-27",
        window_hit_rate=0.1, window_n=200, consecutive_windows_failed=3, detail="d",
    )


def test_single_window_failure_never_proposes_retirement(tmp_path: Path) -> None:
    result = rr.propose_retirement(
        SetupKind.NR7_BREAKOUT, "nse", _failure(FailureSeverity.SINGLE),
        registry_path=tmp_path / "registry.sqlite",
    )
    assert result is None


def test_sustained_failure_with_no_lab_replacement_proposes_bare_retirement(
    tmp_path: Path,
) -> None:
    result = rr.propose_retirement(
        SetupKind.NR7_BREAKOUT, "nse", _failure(FailureSeverity.SUSTAINED),
        registry_path=tmp_path / "registry.sqlite",  # does not exist
    )
    assert result is not None
    assert result.setup == "nr7_breakout"
    assert result.market == "nse"
    assert result.replacement_source is None


def test_sustained_failure_with_a_real_lab_candidate_proposes_it_as_the_replacement(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.sqlite"
    with Registry(registry_path) as registry:
        experiment_id = registry.begin({"note": "test"})
        candidate_id = registry.candidate(
            experiment_id, "nse:nr7_breakout:replacement", {"p": 1}, {"net_r": 0.2}
        )
        registry.finish(experiment_id, report={"ok": True})

    result = rr.propose_retirement(
        SetupKind.NR7_BREAKOUT, "nse", _failure(FailureSeverity.SUSTAINED),
        registry_path=registry_path,
    )
    assert result is not None
    assert result.replacement_source == f"lab_candidate:{experiment_id}:{candidate_id}"
