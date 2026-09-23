"""Descriptive AEM diagnostics with explicit outcome and session denominators.

These reports describe a repeatedly inspected historical sample. They neither select
thresholds nor turn historical accuracy into eligibility for live calls.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import NormalDist

import numpy as np
import pandas as pd

_UNFILLED_STATUSES = frozenset({"chased", "unsizeable", "no_pullback_fill", "not_triggered"})
_RESOLVED_COLUMNS = ("strict_success", "target_hit", "net_pnl", "net_r")


def _wilson(wins: int, total: int) -> dict:
    if not total:
        return {"confidence": 0.95, "lower": None, "upper": None}
    z = NormalDist().inv_cdf(0.975)
    rate = wins / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    radius /= denominator
    return {
        "confidence": 0.95,
        "lower": max(0.0, center - radius),
        "upper": min(1.0, center + radius),
    }


def _date(value: str) -> str:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("evaluation and event dates must be valid")
    if stamp.tz is not None:
        stamp = stamp.tz_convert("Asia/Kolkata")
    return stamp.date().isoformat()


def _validate_resolved(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    missing = set(_RESOLVED_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"resolved AEM trades are missing {sorted(missing)}")
    for name in ("strict_success", "target_hit"):
        if not frame[name].map(lambda value: isinstance(value, (bool, np.bool_))).all():
            raise ValueError(f"resolved AEM {name} must be boolean and nonmissing")
    for name in ("net_pnl", "net_r"):
        values = pd.to_numeric(frame[name], errors="coerce")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"resolved AEM {name} must be finite and nonmissing")
    pnl = pd.to_numeric(frame.net_pnl)
    net_r = pd.to_numeric(frame.net_r)
    expected = frame.target_hit & (pnl > 0)
    if not (frame.strict_success == expected).all():
        raise ValueError("strict success must mean target hit with positive net P&L")
    if not (np.sign(pnl) == np.sign(net_r)).all():
        raise ValueError("resolved net P&L and net R must have matching signs")
    if "label" in frame:
        labels = pd.to_numeric(frame.label, errors="coerce")
        if not (labels == expected.astype(int)).all():
            raise ValueError("resolved label contradicts strict success or is missing")


def _summary(frame: pd.DataFrame) -> dict:
    resolved = frame.loc[frame._status == "resolved"]
    total = len(resolved)
    wins = int(resolved.strict_success.sum()) if total else 0
    positive = int((pd.to_numeric(resolved.net_pnl) > 0).sum()) if total else 0
    trades = frame.loc[frame.decision == "TRADE"]
    unfilled = int(trades._status.isin(_UNFILLED_STATUSES).sum())
    unresolved = len(trades) - total - unfilled
    return {
        "candidate_sessions": len(frame),
        "trade_decisions": len(trades),
        "resolved_trades": total,
        "unfilled_decisions": unfilled,
        "unresolved_decisions": unresolved,
        "strict_successes": wins,
        "strict_success_rate": wins / total if total else None,
        "strict_success_wilson95": _wilson(wins, total),
        "positive_net_trades": positive,
        "positive_net_rate": positive / total if total else None,
        "mean_net_r": float(pd.to_numeric(resolved.net_r).mean()) if total else None,
        "decisions": dict(sorted(Counter(frame.decision).items())),
        "statuses": dict(sorted(Counter(frame._status).items())),
    }


def summarize_aem(events: pd.DataFrame, evaluation_dates: list[str]) -> dict:
    """Summarize resolved fills without counting no-fills or unknown outcomes as losses.

    Session-target proportions use only active sessions with fully known outcomes.
    Every supplied evaluation session is retained, including sessions with zero calls.
    This function is reporting only: it does not infer missing outcomes or select a model.
    """
    dates = [_date(value) for value in evaluation_dates]
    if len(set(dates)) != len(dates):
        raise ValueError("evaluation_dates must contain distinct trading sessions")
    dates = sorted(dates)
    frame = events.copy()
    if frame.empty:
        frame = pd.DataFrame(columns=["session_date", "decision", "_status"])
    else:
        missing = {"session_date", "decision"}.difference(frame.columns)
        if missing:
            raise ValueError(f"AEM events are missing {sorted(missing)}")
        frame["session_date"] = frame.session_date.map(_date)
        if not frame.session_date.isin(dates).all():
            raise ValueError("AEM event falls outside the supplied evaluation sessions")
        if not frame.decision.isin({"TRADE", "WATCH", "NO_TRADE"}).all():
            raise ValueError("AEM events contain a missing or unknown decision")
        if "event_id" in frame and (
            frame.event_id.isna().any() or frame.event_id.duplicated().any()
        ):
            raise ValueError("AEM event identifiers must be present and unique")
        status = frame.get("status", pd.Series(index=frame.index, dtype=object))
        frame["_status"] = status.where(status.notna(), "missing_status")
        frame.loc[(frame.decision != "TRADE") & status.isna(), "_status"] = "not_triggered"
        if not frame._status.map(lambda value: isinstance(value, str) and bool(value)).all():
            raise ValueError("AEM status must be a nonempty string when present")
        if ((frame._status == "resolved") & (frame.decision != "TRADE")).any():
            raise ValueError("resolved AEM outcomes require a TRADE decision")
        _validate_resolved(frame.loc[frame._status == "resolved"])

    overall = _summary(frame)
    sessions = []
    for day in dates:
        summary = _summary(frame.loc[frame.session_date == day])
        complete = summary["resolved_trades"] > 0 and summary["unresolved_decisions"] == 0
        sessions.append(
            {
                "session_date": day,
                **summary,
                "at_least_70pct": summary["strict_success_rate"] >= 0.70 if complete else None,
                "at_least_80pct": summary["strict_success_rate"] >= 0.80 if complete else None,
            }
        )
    active = [row for row in sessions if row["resolved_trades"] > 0]
    complete_active = [row for row in active if row["unresolved_decisions"] == 0]

    def breakdown(column: str) -> dict:
        if column not in frame:
            return {}
        values = frame[column].fillna("unknown").astype(str)
        return {value: _summary(frame.loc[values == value]) for value in sorted(values.unique())}

    return {
        "status": "historical_diagnostic_only",
        "eligible_for_live": False,
        "never_live": True,
        "overall": overall,
        "session_coverage": {
            "evaluation_sessions": len(dates),
            "active_sessions": len(active),
            "fully_resolved_active_sessions": len(complete_active),
            "sessions_with_unresolved_decisions": sum(
                row["unresolved_decisions"] > 0 for row in sessions
            ),
            "zero_trade_decision_sessions": sum(row["trade_decisions"] == 0 for row in sessions),
            "zero_resolved_trade_sessions": len(dates) - len(active),
            "mean_resolved_trades_per_session": overall["resolved_trades"] / len(dates)
            if dates
            else None,
            "active_sessions_at_least_70pct": sum(row["at_least_70pct"] for row in complete_active)
            / len(complete_active)
            if complete_active
            else None,
            "active_sessions_at_least_80pct": sum(row["at_least_80pct"] for row in complete_active)
            / len(complete_active)
            if complete_active
            else None,
        },
        "by_session": sessions,
        "by_symbol": breakdown("symbol" if "symbol" in frame else "scrip_code"),
        "by_entry_pattern": breakdown(
            "intraday_entry_pattern" if "intraday_entry_pattern" in frame else "entry_style"
        ),
        "definitions": {
            "strict_success": "Target reached and net P&L after costs and slippage is positive.",
            "positive_net": "Any resolved trade with net P&L > 0, including profitable time exits.",
            "rate_denominator": (
                "Resolved filled trades only; no-fills and unknown outcomes excluded."
            ),
            "session_target_denominator": (
                "Sessions with resolved trades and no unresolved decisions; excludes zero-call "
                "and incomplete-outcome sessions, whose counts are reported separately."
            ),
            "unresolved_decisions": (
                "TRADE decisions lacking a resolved result or an explicit unfilled status."
            ),
            "zero_trade_decision_sessions": (
                "Sessions with no recorded TRADE event; this does not establish that complete "
                "market data existed or that no valid opportunity was missed."
            ),
        },
        "limitations": [
            "This historical sample has been reused and tuned; these are development diagnostics, "
            "not untouched out-of-sample or prospective evidence.",
            "Wilson intervals are IID diagnostics; correlated stocks and sessions reduce the "
            "effective sample size.",
            "Observed active-session rates exclude zero-call and incomplete-outcome sessions; "
            "they do not establish a 70-80% success rate in every future session.",
            "The supplied evaluation calendar establishes the reporting denominator, not proof "
            "that every NSE stock had usable data in every session. This event-only report "
            "cannot distinguish missing-market-data days from valid no-call days; consult "
            "the source coverage and data-rejection audit.",
            "Trade-level results do not simulate concurrent portfolio capital, sector, "
            "or heat limits.",
            "No matched-random validation gate has been passed: a valid AEM baseline must "
            "reproduce the same market/limit entry, fill, cost, and holding-time contract.",
            "Passing software tests validates reporting behavior, not a 70-80% trading "
            "success rate or eligibility for live calls.",
        ],
    }
