"""Milestone 4: mandatory execution-stress gates for a nominated AEM v2 pipeline.

A specification that clears every development gate in the pipeline race
(``aem_v2_pipeline_race.py``) is only a *nomination*. The frozen protocol
(``aem_v2_contract.DEFAULT_AEM_V2_PROTOCOL.mandatory_stress_cases``) requires that
nomination to survive registered execution stresses before it can be called a research
baseline. This module re-resolves exactly the fills the nominated ``(spec_id, top_k)``
policy actually selected - via the same deterministic nested walk-forward selection
(``aem_v2_pipeline_race.selected_fills_for_spec``) - under the six registered stress
cases, mirroring AEM v1's own precedent (``aem_staged_validation.py``): the base case,
cost inflation (1.25x / 1.5x), doubled slippage, a one-minute execution delay, and an
adversarial "best 10% of fills never execute" case. The pass bar is the same one AEM
v1's own scorecard uses (``aem_scorecard.py``'s ``stress_economics`` gate): the
worst-case mean net R across every registered stress must remain positive.

This never re-derives the fill; it re-resolves the SAME opportunity/geometry pair
through the real ``resolve_opportunity`` engine with a harsher cost model, a delayed
decision timestamp, or (for the adversarial case) treated as never filled - the same
causal, fail-closed mechanics Milestone 1 already established, not a shortcut.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT, DEFAULT_AEM_V2_PROTOCOL
from tradedesk_lab.aem_v2_development_experiment import (
    RISK_PCT,
    fixed_risk_quantity,
    load_real_session_sources,
)
from tradedesk_lab.aem_v2_events import M1, reconstruct_session_opportunities, resolve_opportunity
from tradedesk_lab.aem_v2_pipeline_race import _load_development_dataset, selected_fills_for_spec
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

MISSED_FILL_ADVERSE_FRACTION = 0.10


@dataclass(frozen=True)
class _CostStress:
    """CostModel view used only by this replay; charges and slippage scale separately.
    Mirrors ``aem_staged_validation._CostStress`` for AEM v2's own cost/opportunity
    shape (``round_trip_cost`` rather than ``leg_cost``).
    """

    base: Any
    charge_multiplier: Decimal = Decimal("1")
    slippage_multiplier: Decimal = Decimal("1")

    @property
    def slippage_pct(self) -> Decimal:
        return self.base.slippage_pct * self.slippage_multiplier

    def round_trip_cost(self, **kwargs: Any) -> Any:
        rt = self.base.round_trip_cost(**kwargs)
        return rt.model_copy(update={"total": rt.total * self.charge_multiplier})


def _stress_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = len(rows)
    resolved = [row for row in rows if row["status"] == "resolved"]
    unresolved = [row for row in rows if row["status"] not in {"resolved", "missed_fill"}]
    if unresolved:
        raise ValueError(
            "unresolved_stress_outcomes:" + ",".join(sorted({r["status"] for r in unresolved}))
        )
    wins = sum(bool(row.get("strict_success")) for row in resolved)
    values = [float(row["net_r"]) for row in resolved]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("nonfinite_stress_net_r")
    return {
        "attempts": attempts,
        "resolved_fills": len(resolved),
        "no_fills": attempts - len(resolved),
        "strict_successes": wins,
        "strict_success_rate": wins / len(resolved) if resolved else None,
        "mean_net_r": float(np.mean(values)) if values else None,
    }


def _resolve_one(
    row: pd.Series,
    *,
    sources: dict[str, Any],
    equity: float,
    base_costs: Any,
    charge_multiplier: Decimal,
    slippage_multiplier: Decimal,
    delay_minutes: int,
) -> dict[str, Any]:
    code, session_str = row["scrip_code"], row["session"]
    session_frame = sources["per_code_sessions"][code][session_str]
    opportunities = reconstruct_session_opportunities(
        session_frame, scrip_code=code, symbol=sources["symbols"][code]
    )
    opportunity = next(o for o in opportunities if o.identifier == row["opportunity_id"])
    if delay_minutes:
        decision_at = pd.Timestamp(opportunity.decision_at) + delay_minutes * M1
        opportunity = replace(opportunity, decision_at=decision_at.isoformat())
    geometry = next(g for g in DEFAULT_AEM_V2_CONTRACT.geometries if g.id == row["geometry_id"])
    stop = opportunity.intended_entry * (1 - geometry.stop_pct)
    quantity = fixed_risk_quantity(
        opportunity.intended_entry, stop, equity=equity, risk_pct=RISK_PCT
    )
    if quantity <= 0:
        return {"opportunity_id": row["opportunity_id"], "status": "no_fill", "net_r": None}
    costs = _CostStress(
        base_costs, charge_multiplier=charge_multiplier, slippage_multiplier=slippage_multiplier
    )
    outcome = resolve_opportunity(
        opportunity, session_frame, geometry, quantity=quantity, costs=costs
    )
    return {
        "opportunity_id": row["opportunity_id"],
        "status": outcome["status"],
        "strict_success": outcome.get("strict_success"),
        "net_r": outcome.get("net_r"),
    }


def run_stress_gates(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    spec_id: str,
    top_k: int,
    dataset_run_id: str | None = None,
    audit_id: str | None = None,
    splits: int = 3,
    embargo: int = 10,
    seed: int = 20260101,
) -> dict[str, Any]:
    """Re-resolve the real fills a nominated ``(spec_id, top_k)`` selected, under every
    registered stress case, and report the worst-case mean net R.
    """

    root, output = Path(root).resolve(), Path(output).resolve()
    dataset_run_id, dataset = _load_development_dataset(output, dataset_run_id)
    selected = selected_fills_for_spec(
        dataset, spec_id, top_k=top_k, splits=splits, embargo=embargo, seed=seed
    )
    if selected.empty:
        raise ValueError("nominated spec/top_k selected no fills - nothing to stress")

    sources = load_real_session_sources(root, output, audit_id=audit_id)
    equity = float(sources["settings"].risk.trading_capital)
    base_costs = sources["market"].costs

    definitions: dict[str, tuple[Decimal, Decimal, int]] = {
        "base_costs": (Decimal("1"), Decimal("1"), 0),
        "costs_1p25x": (Decimal("1.25"), Decimal("1"), 0),
        "costs_1p50x": (Decimal("1.50"), Decimal("1"), 0),
        "slippage_2x": (Decimal("1"), Decimal("2"), 0),
        "one_bar_delay": (Decimal("1"), Decimal("1"), 1),
    }
    resolved_by_case: dict[str, list[dict[str, Any]]] = {}
    for name, (charge_mult, slip_mult, delay) in definitions.items():
        resolved_by_case[name] = [
            _resolve_one(
                row,
                sources=sources,
                equity=equity,
                base_costs=base_costs,
                charge_multiplier=charge_mult,
                slippage_multiplier=slip_mult,
                delay_minutes=delay,
            )
            for _, row in selected.iterrows()
        ]

    # Adversarial case: the best-net-R fraction of the base case's resolved fills never
    # execute at all - the same "if it worked, it probably didn't fill" sensitivity
    # AEM v1's own validation applies.
    base_rows = resolved_by_case["base_costs"]
    base_resolved = sorted(
        (row for row in base_rows if row["status"] == "resolved"),
        key=lambda row: (-float(row["net_r"]), row["opportunity_id"]),
    )
    miss_count = math.ceil(len(base_resolved) * MISSED_FILL_ADVERSE_FRACTION)
    missed_ids = {row["opportunity_id"] for row in base_resolved[:miss_count]}
    resolved_by_case["adverse_missed_fills"] = [
        (
            {"opportunity_id": row["opportunity_id"], "status": "missed_fill", "net_r": None}
            if row["opportunity_id"] in missed_ids
            else dict(row)
        )
        for row in base_rows
    ]

    cases = {name: _stress_stats(rows) for name, rows in resolved_by_case.items()}
    expected = list(DEFAULT_AEM_V2_PROTOCOL.mandatory_stress_cases)
    if sorted(expected) != sorted(cases):
        raise ValueError("stress cases evaluated do not match the frozen protocol's list")
    means = [cases[name]["mean_net_r"] for name in expected]
    if any(value is None for value in means):
        raise ValueError("registered stress case has no resolved fills")
    minimum_mean_net_r = min(means)

    return {
        "spec_id": spec_id,
        "top_k": top_k,
        "dataset_run_id": dataset_run_id,
        "selected_fills": int(len(selected)),
        "cases": cases,
        "minimum_mean_net_r": minimum_mean_net_r,
        "passed": minimum_mean_net_r > 0,
        "mandatory_stress_cases": expected,
    }


def freeze_stress_gates(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    spec_id: str,
    top_k: int,
    dataset_run_id: str | None = None,
    audit_id: str | None = None,
    splits: int = 3,
    embargo: int = 10,
    seed: int = 20260101,
) -> dict[str, Any]:
    root, output = Path(root).resolve(), Path(output).resolve()
    result = run_stress_gates(
        root,
        output,
        spec_id=spec_id,
        top_k=top_k,
        dataset_run_id=dataset_run_id,
        audit_id=audit_id,
        splits=splits,
        embargo=embargo,
        seed=seed,
    )
    run_id = uuid4().hex
    target = output / "aem_v2/stress_gates/runs" / run_id
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "aem-v2-milestone-4-stress-gates-v1",
        "status": "stress_gates_evaluated",
        "milestone": 4,
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_evaluated": True,
        **result,
        "implementation_sha256": digest(Path(__file__)),
        "decision": {
            "register_candidate": bool(result["passed"]),
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "reason": (
                "This nominated pipeline's real, selected fills kept a positive mean "
                "net R under every registered execution stress. It still needs "
                "Milestone 5's independent locked evaluation on unconsumed data before "
                "it could be called a research baseline."
                if result["passed"]
                else "This nominated pipeline's real, selected fills failed at least "
                "one registered execution stress (worst-case mean net R "
                f"{result['minimum_mean_net_r']:.4f}). It is not promoted; AEM v1 "
                "remains the canonical baseline; no live change."
            ),
        },
    }
    write_json(target / "report.json", report)
    write_json(
        output / "aem_v2/stress_gates/latest.json",
        {"id": run_id, "path": str(target / "report.json")},
    )
    return report
