"""Retire/replace proposals (Phase 2) for a setup the rolling-failure monitor has confirmed
SUSTAINED (not single-window) failure on.

Unlike a config-tuning proposal, a bare retirement makes no new positive performance claim -
it turns off something that is ALREADY failing, on real, sustained, forward-collected
evidence (the `SustainedFailure` record itself). There is no candidate strategy to run
through the harness gauntlet here, so none is run for the retire-only case; the gauntlet
requirement in this feature's design exists to keep a NEW claim honest, and retiring
something makes no new claim. A REPLACEMENT, if one is proposed, is always a separate
ConfigPatch/NewDetectorCode proposal with its own gauntlet validation - retirement and
replacement are never bundled into one approval (see `decision_packet.py`).

Today, `find_lab_replacement` almost always returns None: nothing yet writes
Setup-protocol-compatible candidates into `tradedesk_lab/registry.py`'s SQLite under a
`<market>:<setup>:replacement` family - the lab registry's existing candidates come from a
structurally different research line (AEM's own resolved-trade model). This function is
still real, correct code, not a stub - it will pick up a genuine replacement the moment
something (a future Phase-3 NewDetectorCode authoring run, or the AEM v2 work itself) starts
registering candidates under that family/metric convention.
"""

from __future__ import annotations

from pathlib import Path

from tradedesk_lab.artifacts import OUTPUT
from tradedesk_lab.registry import Registry

from tradedesk.engine.signals import SetupKind
from tradedesk.proposals import RetireSetup
from tradedesk.rolling_failure_monitor import FailureSeverity, SustainedFailure

REGISTRY_METRIC = "net_r"


def find_lab_replacement(
    setup_kind: SetupKind, market: str, *, registry_path: Path = OUTPUT / "registry.sqlite"
) -> tuple[str, str] | None:
    """Returns (experiment_id, candidate_id) of the best already-gauntlet-passed lab
    candidate registered for this exact (market, setup) replacement family, or None."""

    if not registry_path.exists():
        return None
    # The per-setup family (a candidate authored for this one setup) first, then the
    # market-wide family the replacement search registers everything under.
    with Registry(registry_path, readonly=True) as registry:
        for family in (f"{market}:{setup_kind.value}:replacement", f"{market}:replacement"):
            best = registry.best(metric=REGISTRY_METRIC, family=family)
            if best is not None:
                return best["experiment_id"], best["id"]
    return None


def propose_retirement(
    setup_kind: SetupKind,
    market: str,
    failure: SustainedFailure,
    *,
    registry_path: Path = OUTPUT / "registry.sqlite",
    already_retired: bool = False,
    replacement_plan: str | None = None,
    replacement_search_report: str | None = None,
) -> RetireSetup | None:
    """Only ever proposes retirement for SUSTAINED (consecutive-window) failure - a SINGLE
    bad window is the config-tuning path's trigger, not this one. Returns None (proposes
    nothing) for a single-window failure, matching the user's explicit requirement that
    retiring or rewriting a setup must follow continuous failure, never one flag - and for
    a setup already retired on this market (it keeps failing in shadow; that is expected,
    not a reason to ask again)."""

    if failure.severity != FailureSeverity.SUSTAINED or already_retired:
        return None
    replacement = find_lab_replacement(setup_kind, market, registry_path=registry_path)
    # The registry's candidate record doesn't itself carry a production SetupKind (it may
    # not even correspond to an existing one yet, if it needs Phase-3 authoring first) - only
    # `replacement_source` (a pointer a human/Phase-3 can follow) is populated here.
    replacement_source = f"lab_candidate:{replacement[0]}:{replacement[1]}" if replacement else None
    return RetireSetup(
        setup=setup_kind.value,
        market=market,
        replacement_setup_kind=None,
        replacement_source=replacement_source,
        replacement_plan=replacement_plan,
        replacement_search_report=replacement_search_report,
    )
