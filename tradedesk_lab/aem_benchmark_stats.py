"""Fail-closed, descriptive accounting for matched AEM timing-null cohorts.

The opportunity population is fixed: an explicit no-fill contributes zero net R,
not a silently removed observation. This report is neither a portfolio backtest
nor a confirmatory statistical test on untouched data.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime
from numbers import Real

import numpy as np
import pandas as pd

_NOFILLS = frozenset({"chased", "unsizeable", "no_pullback_fill"})
_STATUSES = _NOFILLS | {"resolved"}
_BASE_COLUMNS = {"event_id", "session_date", "status"}


def _number(value: object, field: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite real number, not a string or boolean")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite and nonmissing")
    return result


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{field} must be boolean and nonmissing")
    return bool(value)


def _session(value: object) -> str:
    if not isinstance(value, (str, date, datetime, pd.Timestamp)):
        raise ValueError("session_date must identify a valid date")
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError("missing session_date")
        if stamp.tz is not None:
            stamp = stamp.tz_convert("Asia/Kolkata")
        if stamp != stamp.normalize():
            raise ValueError("session_date must not contain a time of day")
        return stamp.date().isoformat()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("session_date must identify a valid date without a time of day") from exc


def _is_missing(value: object) -> bool:
    return (
        value is None
        or value is pd.NA
        or (isinstance(value, (float, np.floating)) and math.isnan(float(value)))
    )


def _validate_outcome(row: pd.Series) -> tuple[float, bool]:
    if row.status != "resolved":
        for field in ("net_r", "net_pnl", "label"):
            value = row.get(field)
            if not _is_missing(value) and _number(value, field) != 0:
                raise ValueError(f"no-fill {field} must be missing or zero")
        for field in ("strict_success", "target_hit"):
            value = row.get(field)
            if not _is_missing(value) and _boolean(value, field):
                raise ValueError(f"no-fill {field} cannot be true")
        return 0.0, False

    net_r = _number(row.get("net_r"), "resolved net_r")
    strict = _boolean(row.get("strict_success"), "resolved strict_success")
    if strict and net_r <= 0:
        raise ValueError("strict_success requires positive net R")
    if "target_hit" in row:
        target = _boolean(row.target_hit, "resolved target_hit")
        if strict != (target and net_r > 0):
            raise ValueError("strict_success must mean a target hit with positive net R")
    if "net_pnl" in row:
        pnl = _number(row.net_pnl, "resolved net_pnl")
        if np.sign(pnl) != np.sign(net_r):
            raise ValueError("net_pnl and net_r must have matching signs")
    if "label" in row and _number(row.label, "resolved label") != int(strict):
        raise ValueError("resolved label must equal strict_success")
    return net_r, strict


def _validate(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if not frame.columns.is_unique:
        raise ValueError(f"{name} contains duplicate column names")
    missing = _BASE_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"{name} is missing {sorted(missing)}")
    frame = frame.copy()
    if not frame.event_id.map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError(f"{name} event_id must be a nonempty string")
    if frame.event_id.duplicated().any():
        raise ValueError(f"{name} event_id must be unique")
    frame["session_date"] = frame.session_date.map(_session)
    if not frame.status.isin(_STATUSES).all():
        raise ValueError(f"{name} has an unresolved, missing, or unknown status")
    if "decision" in frame and not frame.decision.eq("TRADE").all():
        raise ValueError(f"{name} must contain TRADE attempts only")
    outcomes = [_validate_outcome(row) for _, row in frame.iterrows()]
    frame["_attempt_net_r"] = [outcome[0] for outcome in outcomes]
    frame["_strict_success"] = [outcome[1] for outcome in outcomes]
    return frame.sort_values("event_id").reset_index(drop=True)


def _finite_mean(values: list[float]) -> float:
    # Dividing first avoids overflow when a finite mean has an unrepresentable sum.
    result = math.fsum(value / len(values) for value in values)
    if not math.isfinite(result):
        raise ValueError("aggregate net R must remain finite")
    return result


def _stats(frame: pd.DataFrame) -> dict:
    attempts = len(frame)
    filled = frame.loc[frame.status == "resolved"]
    successes = int(filled._strict_success.sum())
    return {
        "attempts": attempts,
        "resolved_fills": len(filled),
        "no_fills": attempts - len(filled),
        "fill_rate": len(filled) / attempts if attempts else None,
        "strict_successes": successes,
        "strict_success_rate_per_fill": successes / len(filled) if len(filled) else None,
        "mean_net_r_per_fill": _finite_mean(filled._attempt_net_r.tolist())
        if len(filled)
        else None,
        "mean_net_r_per_attempt": _finite_mean(frame._attempt_net_r.tolist()) if attempts else None,
        "statuses": dict(sorted(Counter(frame.status).items())),
    }


def _quantiles(values: list[float]) -> dict:
    names = ("p02_5", "p05", "p50", "p95", "p97_5")
    ordered = sorted(values)
    result = []
    for probability in (0.025, 0.05, 0.5, 0.95, 0.975):
        location = (len(values) - 1) * probability
        lower, upper = math.floor(location), math.ceil(location)
        weight = location - lower
        # A convex sum avoids overflow in the usual (upper - lower) interpolation.
        result.append(ordered[lower] * (1 - weight) + ordered[upper] * weight)
    return dict(zip(names, result, strict=True))


def summarize_timing_null(actual: pd.DataFrame, cohorts: pd.DataFrame) -> dict:
    """Compare equal event populations using zero net R for explicit no-fills.

    Resolved rows require finite numeric ``net_r`` and boolean ``strict_success``.
    Optional ``target_hit``, ``net_pnl`` and ``label`` columns, when present, must
    also be nonmissing and consistent on resolved rows. Each cohort must contain
    every actual event exactly once with the same session date. Unknown outcomes
    invalidate the comparison rather than shrinking either denominator.
    """
    report = {
        "status": "insufficient_data",
        "eligible_for_live": False,
        "never_live": True,
        "evidence_class": "previously_inspected_development_sample",
        "actual": None,
        "cohorts": [],
        "comparison": None,
        "session_coverage": {
            "observed_sessions": 0,
            "attempts_by_session": {},
            "zero_call_sessions": None,
            "evaluation_sessions": None,
        },
        "definitions": {
            "primary_metric": "Mean net R over every attempted opportunity, including no-fills.",
            "no_fill_net_r": (
                "Explicit chased, unsizeable and no_pullback_fill outcomes count as 0R."
            ),
            "strict_success_rate_per_fill": "Strict successes divided by resolved filled trades.",
            "upper_tail_fraction_plus_one": (
                "(1 + cohorts with mean net R per attempt >= actual) / (1 + cohort count). "
                "Descriptive Monte Carlo rank only; NOT a confirmatory p-value."
            ),
        },
        "limitations": [
            "Previously inspected development history, not untouched out-of-sample or "
            "prospective evidence; this report does not tune thresholds.",
            "Matching event IDs and sessions checks denominators, not execution-contract or "
            "sampling validity; those must be checked by the cohort generator.",
            "The supplied rows contain attempted opportunities only. Zero-call days and the "
            "complete evaluation calendar are unknown and are not inferred.",
            "Conditional success rates exclude no-fills and must not replace the all-attempt "
            "primary comparison. No-fill attempts do not incur simulated execution costs.",
            "Trade-level net R is not a portfolio return: concurrent capital, sector, heat, "
            "capacity and correlation constraints are not simulated.",
            "Neither a favorable descriptive comparison nor software tests establish a "
            "70-80% future-session success rate or eligibility for live trading.",
        ],
    }
    if actual.empty:
        if not cohorts.empty:
            raise ValueError("cohorts cannot contain events when actual has no attempts")
        report["reason"] = "No actual attempts or null cohorts supplied."
        return report

    actual = _validate(actual, "actual")
    report["actual"] = _stats(actual)
    counts = actual.groupby("session_date", sort=True).size()
    report["session_coverage"]["observed_sessions"] = len(counts)
    report["session_coverage"]["attempts_by_session"] = {
        day: int(count) for day, count in counts.items()
    }
    if cohorts.empty:
        report["reason"] = "No null cohorts supplied; no comparison is available."
        return report
    if not cohorts.columns.is_unique or "cohort_id" not in cohorts:
        raise ValueError("cohorts require one unique cohort_id column")

    def valid_cohort_id(value: object) -> bool:
        return (isinstance(value, str) and bool(value.strip())) or (
            isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_))
        )

    if not cohorts.cohort_id.map(valid_cohort_id).all():
        raise ValueError("cohort_id must be a nonempty string or integer")
    identities = actual[["event_id", "session_date"]]
    summaries = []
    grouped = cohorts.groupby("cohort_id", sort=False)
    for cohort_id, rows in sorted(grouped, key=lambda item: (str(type(item[0])), str(item[0]))):
        cohort = _validate(rows, f"cohort {cohort_id}")
        if not identities.equals(cohort[["event_id", "session_date"]]):
            raise ValueError("every cohort must contain the same exact event_id/session_date pairs")
        identifier = int(cohort_id) if isinstance(cohort_id, np.integer) else cohort_id
        summaries.append({"cohort_id": identifier, **_stats(cohort)})

    means = [summary["mean_net_r_per_attempt"] for summary in summaries]
    null_mean = _finite_mean(means)
    actual_mean = report["actual"]["mean_net_r_per_attempt"]
    gap = actual_mean - null_mean
    if not math.isfinite(gap):
        raise ValueError("actual-minus-null net R gap must remain finite")
    upper_tail_count = sum(value >= actual_mean for value in means)
    report["status"] = "historical_diagnostic_only"
    report["cohorts"] = summaries
    report["comparison"] = {
        "metric": "mean_net_r_per_attempt",
        "cohort_count": len(summaries),
        "actual_mean_net_r_per_attempt": actual_mean,
        "null_mean_net_r_per_attempt": null_mean,
        "actual_minus_null_mean_net_r": gap,
        "null_cohort_mean_quantiles": _quantiles(means),
        "null_cohorts_at_least_actual": upper_tail_count,
        "descriptive_upper_tail_fraction_plus_one": (upper_tail_count + 1) / (len(means) + 1),
        "is_confirmatory_p_value": False,
    }
    return report
