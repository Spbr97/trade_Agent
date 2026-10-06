"""Evidence-labelled failure attribution without rewriting sealed predictions.

Attributions describe observed paths or diagnostic associations. They are never treated as
causal proof and never convert invalid records into performance evidence.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("prediction_payload")
    return value if isinstance(value, Mapping) else {}


def _add(
    out: list[dict[str, Any]], code: str, evidence: str, reason: str, **facts: Any
) -> None:
    out.append({"code": code, "evidence": evidence, "reason": reason, "facts": facts})


def attribute_failure(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic attributions for one terminal record.

    `verified_path` means the category follows directly from recorded prices/events.
    `diagnostic_association` means it is a testable association, not a proven cause.
    """

    state = record.get("outcome_state")
    if state == "invalid_call":
        return [
            {
                "code": "data_quality_failure",
                "evidence": "verified_system",
                "reason": "record is invalid/unavailable and excluded from performance learning",
                "facts": {"event": record.get("first_event") or record.get("outcome")},
            }
        ]
    if state != "resolved_call" or record.get("label") != 0:
        return []

    payload = _payload(record)
    context = payload.get("context") if isinstance(payload.get("context"), Mapping) else {}
    source = (
        payload.get("source_snapshot")
        if isinstance(payload.get("source_snapshot"), Mapping)
        else {}
    )
    source_bar = (
        source.get("source_feature_row")
        if isinstance(source.get("source_feature_row"), Mapping)
        else {}
    )
    out: list[dict[str, Any]] = []
    outcome = record.get("outcome")
    mfe = record.get("mfe_r")
    mae = record.get("mae_r")
    probability = record.get("probability")
    atr_pct = context.get("atr_pct")
    turnover = context.get("average_turnover")
    sector_pct = context.get("sector_percentile")

    if outcome == "gap_stop" or record.get("first_event") == "gap_stop":
        _add(out, "gap_through_stop", "verified_path", "exit opened through the stop")
    if outcome in {"stop", "gap_stop"} and isinstance(mfe, int | float) and mfe < 0.5:
        _add(
            out,
            "false_breakout",
            "verified_path",
            "trade failed before reaching +0.5R favourable excursion",
            mfe_r=mfe,
        )
    actual_entry = record.get("actual_entry_price")
    planned_entry = record.get("entry")
    atr = source_bar.get("atr")
    if all(isinstance(v, int | float) for v in (actual_entry, planned_entry, atr)) and atr:
        extension = (float(actual_entry) - float(planned_entry)) / float(atr)
        if extension >= 0.5:
            _add(
                out,
                "late_or_overextended_entry",
                "verified_path",
                "actual fill was at least 0.5 ATR above the planned trigger",
                extension_atr=extension,
            )
    if isinstance(sector_pct, int | float) and sector_pct < 50:
        _add(
            out,
            "sector_weakness",
            "diagnostic_association",
            "sector percentile was below 50 at prediction time",
            sector_percentile=sector_pct,
        )
    volume_ratio = source_bar.get("volume_ratio", source_bar.get("vol_ratio"))
    if isinstance(volume_ratio, int | float) and volume_ratio < 1.0:
        _add(
            out,
            "insufficient_volume_confirmation",
            "diagnostic_association",
            "prediction-time volume ratio was below 1.0",
            volume_ratio=volume_ratio,
        )
    if isinstance(atr_pct, int | float) and atr_pct > 0.05:
        _add(
            out,
            "excessive_volatility",
            "diagnostic_association",
            "prediction-time ATR exceeded 5% of price",
            atr_pct=atr_pct,
        )
    if isinstance(turnover, int | float) and turnover < 50_000_000 and record.get("market") in {
        "nse",
        "bse",
    }:
        _add(
            out,
            "poor_liquidity_or_slippage",
            "diagnostic_association",
            "prediction-time average turnover was below the equity reference floor",
            average_turnover=turnover,
        )
    gross_r, net_r = record.get("gross_r"), record.get("net_r")
    if isinstance(gross_r, int | float) and isinstance(net_r, int | float):
        drag = float(gross_r) - float(net_r)
        if drag >= 0.2:
            _add(
                out,
                "poor_liquidity_or_slippage",
                "verified_path",
                "fees and slippage reduced the outcome by at least 0.2R",
                cost_drag_r=drag,
            )
    if (
        outcome in {"stop", "gap_stop"}
        and isinstance(mfe, int | float)
        and isinstance(mae, int | float)
        and mfe >= 0.75
        and mae <= -1.0
    ):
        _add(
            out,
            "stop_too_tight_for_path",
            "diagnostic_association",
            "path first made meaningful progress but still crossed the stop",
            mfe_r=mfe,
            mae_r=mae,
        )
    contract = CONTRACT_TARGETS.get(str(record.get("contract_kind")))
    if outcome == "timeout" and isinstance(mfe, int | float) and contract and mfe >= 0.5:
        _add(
            out,
            "target_too_ambitious",
            "diagnostic_association",
            "timeout made progress but did not reach the frozen target",
            mfe_r=mfe,
            target_r=contract,
        )
    if outcome == "timeout" and isinstance(mfe, int | float) and contract and mfe >= 0.8 * contract:
        _add(
            out,
            "holding_period_too_short",
            "diagnostic_association",
            "price approached the target before contract expiry",
            mfe_r=mfe,
        )
    intended_hold = (payload.get("levels") or {}).get("intended_holding_sessions")
    held = record.get("holding_sessions")
    if (
        outcome in {"stop", "gap_stop"}
        and isinstance(held, int | float)
        and isinstance(intended_hold, int | float)
        and intended_hold > 0
        and held >= 0.8 * intended_hold
        and isinstance(mfe, int | float)
        and mfe > 0
    ):
        _add(
            out,
            "holding_period_too_long",
            "diagnostic_association",
            "failure occurred late in the frozen holding window after positive excursion",
            holding_sessions=held,
            intended_holding_sessions=intended_hold,
            mfe_r=mfe,
        )
    if isinstance(probability, int | float) and probability >= 0.70:
        _add(
            out,
            "model_overconfidence",
            "verified_calibration_miss",
            "failed call carried at least 70% predicted success probability",
            probability=probability,
        )
    if not out:
        _add(
            out,
            "unclassified_failure",
            "insufficient_diagnostics",
            "available prediction/path fields do not support a narrower attribution",
        )
    # One call should not double-count the same category when two facts support it.
    deduped: dict[str, dict[str, Any]] = {}
    for item in out:
        deduped.setdefault(str(item["code"]), item)
    return list(deduped.values())


CONTRACT_TARGETS = {"quick_profit": 0.75, "swing": 2.0}


def _mean(values: Iterable[Any]) -> float | None:
    usable = [float(v) for v in values if isinstance(v, int | float) and math.isfinite(v)]
    return sum(usable) / len(usable) if usable else None


def _wilson(successes: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - margin) / denominator, (centre + margin) / denominator


def failure_attribution_summary(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    mature = [r for r in rows if r.get("outcome_state") == "resolved_call"]
    failures = [r for r in mature if r.get("label") == 0]
    invalid = [r for r in rows if r.get("outcome_state") == "invalid_call"]
    by_code: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    evidence: dict[str, Counter[str]] = defaultdict(Counter)
    codes_by_id: dict[str, set[str]] = defaultdict(set)
    for row in failures:
        items = row.get("failure_attributions") or attribute_failure(row)
        for item in items:
            code = str(item["code"])
            by_code[code].append(row)
            evidence[code][str(item["evidence"])] += 1
            codes_by_id[str(row.get("signal_id"))].add(code)

    ordered_failures = sorted(failures, key=lambda r: str(r.get("armed_on") or ""))
    recent = ordered_failures[-20:]
    previous = ordered_failures[-40:-20]

    categories: list[dict[str, Any]] = []
    for code, members in sorted(by_code.items(), key=lambda item: (-len(item[1]), item[0])):
        sessions = {str(r.get("armed_on", ""))[:10] for r in members}
        lower, upper = _wilson(0, len(members))  # post-outcome failure category by definition
        recent_share = (
            sum(code in codes_by_id[str(r.get("signal_id"))] for r in recent) / len(recent)
            if recent
            else None
        )
        previous_share = (
            sum(code in codes_by_id[str(r.get("signal_id"))] for r in previous) / len(previous)
            if previous
            else None
        )
        categories.append(
            {
                "code": code,
                "failures": len(members),
                "share_of_failures": len(members) / len(failures) if failures else None,
                "strict_accuracy": 0.0,
                "accuracy_wilson_95": {"lower": lower, "upper": upper},
                "mean_gross_r": _mean(r.get("gross_r", r.get("r_multiple")) for r in members),
                "mean_net_r": _mean(r.get("net_r") for r in members),
                "mean_after_tax_r": _mean(r.get("after_tax_r") for r in members),
                "sessions": len(sessions),
                "recurring": len(sessions) >= 2,
                "recent_share": recent_share,
                "previous_share": previous_share,
                "recent_trend_delta": (
                    recent_share - previous_share
                    if recent_share is not None and previous_share is not None
                    else None
                ),
                "evidence_levels": dict(evidence[code]),
                "by_setup": dict(Counter(str(r.get("setup") or "unknown") for r in members)),
                "by_contract": dict(
                    Counter(str(r.get("contract_version") or "unknown") for r in members)
                ),
                "by_market": dict(Counter(str(r.get("market") or "unknown") for r in members)),
                "by_sector": dict(
                    Counter(
                        str((_payload(r).get("instrument") or {}).get("sector") or "unknown")
                        for r in members
                    )
                ),
                "by_regime": dict(
                    Counter(
                        str((_payload(r).get("context") or {}).get("market_regime") or "unknown")
                        for r in members
                    )
                ),
                "by_confidence": dict(
                    Counter(
                        "unknown"
                        if not isinstance(r.get("probability"), int | float)
                        else "70%+"
                        if float(r["probability"]) >= 0.7
                        else "50-70%"
                        if float(r["probability"]) >= 0.5
                        else "<50%"
                        for r in members
                    )
                ),
            }
        )
    setup_performance = []
    for setup in sorted({str(r.get("setup") or "unknown") for r in mature}):
        members = [r for r in mature if str(r.get("setup") or "unknown") == setup]
        successes = sum(r.get("label") == 1 for r in members)
        lower, upper = _wilson(successes, len(members))
        setup_performance.append(
            {
                "setup": setup,
                "resolved": len(members),
                "successes": successes,
                "strict_accuracy": successes / len(members),
                "accuracy_wilson_95": {"lower": lower, "upper": upper},
                "mean_gross_r": _mean(r.get("gross_r", r.get("r_multiple")) for r in members),
                "mean_net_r": _mean(r.get("net_r") for r in members),
                "setup_specific_weakness": len(members) >= 5 and successes / len(members) < 0.5,
            }
        )
    contract_performance = []
    for contract in sorted({str(r.get("contract_version") or "unknown") for r in mature}):
        members = [
            r for r in mature if str(r.get("contract_version") or "unknown") == contract
        ]
        successes = sum(r.get("label") == 1 for r in members)
        lower, upper = _wilson(successes, len(members))
        contract_performance.append(
            {
                "contract_version": contract,
                "resolved": len(members),
                "successes": successes,
                "strict_accuracy": successes / len(members),
                "accuracy_wilson_95": {"lower": lower, "upper": upper},
                "mean_gross_r": _mean(
                    r.get("gross_r", r.get("r_multiple")) for r in members
                ),
                "mean_net_r": _mean(r.get("net_r") for r in members),
            }
        )
    return {
        "resolved_calls": len(mature),
        "failed_calls": len(failures),
        "invalid_calls_excluded": len(invalid),
        "categories": categories,
        "setup_performance": setup_performance,
        "contract_performance": contract_performance,
        "contract_accuracy_pooled": False,
        "unavailable_diagnostics": [
            "market_regime_reversal requires an outcome-time regime snapshot",
            "relative_strength_deterioration requires an outcome-time relative-strength snapshot",
        ],
        "interpretation": (
            "Categories are post-outcome diagnostics. verified_path records observed events; "
            "diagnostic_association is not causal proof. Invalid calls are excluded."
        ),
    }
