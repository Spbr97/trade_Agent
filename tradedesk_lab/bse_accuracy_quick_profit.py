"""BSE-only transported quick-profit accuracy experiment.

The experiment deliberately evaluates one geometry that was frozen by the NSE M6
development result before this BSE test was written.  BSE calls, candles, costs and
matched controls remain separate.  Pre-activation BSE rows are useful transport
diagnostics, but only rows armed on/after the activation date can accumulate prospective
qualification evidence.

Nothing in this module changes a live setup, alert, watchlist or dashboard.  A missing
bar, unresolved outcome, missing matched control or unavailable statistic can never pass.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.markets.costs import CostModel
from tradedesk.markets.market import bse_market
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.accuracy_geometry import (
    EntrySpec,
    PriceBar,
    _quick_outcome,
    wilson_lower_bound,
)
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

DEFAULT_EVIDENCE = OUTPUT / "bse_accuracy_quick_profit" / "state.json"
_TERMINAL_VERSION = "bse-accuracy-quick-profit-first-look-v1"

_TERMINAL_RESEARCH_STATUSES = frozenset({"research_qualified", "rejected"})


def _terminal_payload_sha256(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "terminal_first_look"}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _stamp_terminal_result(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("status") not in _TERMINAL_RESEARCH_STATUSES:
        return value
    stamped = dict(value)
    stamped["terminal_first_look"] = {
        "version": _TERMINAL_VERSION,
        "latched_at": value.get("created_at"),
        "result_sha256": _terminal_payload_sha256(value),
    }
    return stamped


def _load_terminal_bse_quick_profit(
    output: Path,
    protocol: Any,
) -> dict[str, Any] | None:
    """Return a verified terminal B1 artifact, if one has already been recorded.

    Terminal evidence is deliberately fail-closed.  Once the registered first look
    has made a decision, a malformed or incompatible artifact must not silently
    trigger a second look over a larger sample.
    """

    if not output.exists():
        return None
    try:
        value = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BSE B1 evidence artifact is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("BSE B1 evidence artifact is not an object")
    if value.get("status") not in _TERMINAL_RESEARCH_STATUSES:
        return None

    artifact_protocol = value.get("protocol")
    errors: list[str] = []
    if value.get("schema_version") != "bse-accuracy-quick-profit-state-v1":
        errors.append("schema_version")
    if value.get("market") != "bse":
        errors.append("market")
    if (
        not isinstance(artifact_protocol, dict)
        or artifact_protocol.get("sha256") != protocol.sha256
    ):
        errors.append("protocol.sha256")
    if value.get("live") is not False:
        errors.append("live")
    if value.get("promotion_allowed") is not False:
        errors.append("promotion_allowed")
    if value.get("baseline_improved") is not False:
        errors.append("baseline_improved")
    terminal = value.get("terminal_first_look")
    if not isinstance(terminal, dict) or terminal.get("version") != _TERMINAL_VERSION:
        errors.append("terminal_first_look")
    elif terminal.get("result_sha256") != _terminal_payload_sha256(value):
        errors.append("result_sha256")
    prospective = value.get("prospective")
    if not isinstance(prospective, dict):
        errors.append("prospective")
    else:
        trials = prospective.get("trials")
        qualified = prospective.get("qualified_rules")
        if not isinstance(trials, list) or len(trials) != len(CANDIDATE_RULES):
            errors.append("prospective.trials")
        elif not all(
            isinstance(row, dict)
            and row.get("verdict") in {"rejected", "qualified_research_only"}
            for row in trials
        ):
            errors.append("prospective.decisions")
        if not isinstance(qualified, list):
            errors.append("prospective.qualified_rules")
        elif value.get("status") == "research_qualified" and not qualified:
            errors.append("research_qualified")
        elif value.get("status") == "rejected" and qualified:
            errors.append("rejected")
    if errors:
        fields = ", ".join(errors)
        raise ValueError(f"terminal BSE B1 evidence failed verification: {fields}")
    return value

CONTROL_RULE = "random_eligible"
CANDIDATE_RULES = (
    "rsi2_dip_ema50",
    "rsi2_dip_vol",
    "rsi2_deep_ema200",
    "pullback_ema10_above_ema50",
)
FROZEN_RULES = (CONTROL_RULE, *CANDIDATE_RULES)


@dataclass(frozen=True)
class BseQuickProfitProtocol:
    """One transported geometry and the complete fail-closed evidence contract."""

    version: str = "bse-accuracy-quick-profit-v2"
    state_schema_version: str = "bse-accuracy-quick-profit-state-v1"
    market: str = "bse"
    source_log: str = "data/reports/research_calls_bse.jsonl"
    source_db: str = "data/bse.duckdb"
    activation_date: str = "2026-10-04"
    registration_timing: str = "logged_on_arming_session"
    provenance: str = "NSE M6 run 20261003-192811 development diagnostic"
    entry_mode: str = "next_session_open"
    stop_atr: float = 1.0
    target_r: float = 0.5
    max_hold_sessions: int = 3
    min_resolved_calls: int = 100
    min_active_sessions: int = 30
    min_matched_control_sessions: int = 30
    min_session_coverage: float = 0.40
    min_observed_success: float = 0.80
    min_wilson_lower: float = 0.70
    min_session_target_rate: float = 0.70
    min_after_cost_expectancy_r: float = 0.0
    min_control_advantage_r: float = 0.10
    familywise_alpha: float = 0.05
    random_seed: int = 20261004
    sign_flip_draws: int = 16_384

    @property
    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def activation_on(self) -> date:
        return date.fromisoformat(self.activation_date)


DEFAULT_BSE_QUICK_PROFIT_PROTOCOL = BseQuickProfitProtocol()


@dataclass(frozen=True)
class QuickProfitRecord:
    call_id: str
    rule: str
    scrip_code: str
    armed_on: date
    entry_on: date | None
    status: str
    strict_success: int | None
    net_r: float | None


def _source_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _safe_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date.min


def _date_index(bars: pd.DataFrame) -> np.ndarray[Any, np.dtype[np.object_]]:
    index = pd.DatetimeIndex(bars.index)
    if index.tz is None:
        index = index.tz_localize(IST)
    else:
        index = index.tz_convert(IST)
    return np.asarray(index.date, dtype=object)


def resolve_quick_profit_call(
    row: dict[str, Any],
    bars: pd.DataFrame | None,
    *,
    qty: float,
    costs: CostModel,
    protocol: BseQuickProfitProtocol = DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
) -> QuickProfitRecord:
    """Resolve one frozen call with the tested M6 conservative price-path primitive."""

    call_id = str(row.get("call_id", ""))
    rule = str(row.get("rule", ""))
    code = str(row.get("scrip_code", ""))
    try:
        armed_on = date.fromisoformat(str(row["armed_on"]))
    except (KeyError, TypeError, ValueError):
        armed_on = date.min
        return QuickProfitRecord(
            call_id, rule, code, armed_on, None, "invalid_armed_on", None, None
        )
    if not code.startswith("BSE_"):
        return QuickProfitRecord(
            call_id, rule, code, armed_on, None, "excluded_non_bse", None, None
        )
    if rule not in FROZEN_RULES:
        return QuickProfitRecord(
            call_id, rule, code, armed_on, None, "excluded_unregistered_rule", None, None
        )
    if bars is None or bars.empty:
        return QuickProfitRecord(call_id, rule, code, armed_on, None, "missing_bars", None, None)
    try:
        atr = float(row["atr_at_arm"])
    except (KeyError, TypeError, ValueError):
        atr = float("nan")
    if not math.isfinite(atr) or atr <= 0:
        return QuickProfitRecord(call_id, rule, code, armed_on, None, "invalid_atr", None, None)
    if not math.isfinite(qty) or qty <= 0:
        return QuickProfitRecord(call_id, rule, code, armed_on, None, "untradeable", None, None)

    dates = _date_index(bars)
    next_positions = np.flatnonzero(dates > armed_on)
    if not len(next_positions):
        return QuickProfitRecord(
            call_id, rule, code, armed_on, None, "awaiting_entry_session", None, None
        )
    entry_pos = int(next_positions[0])
    required = protocol.max_hold_sessions + 1
    future = bars.iloc[entry_pos : entry_pos + required]
    entry_on = dates[entry_pos]
    if len(future) < required:
        return QuickProfitRecord(
            call_id, rule, code, armed_on, entry_on, "awaiting_outcome_sessions", None, None
        )
    ohlc = future[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
        return QuickProfitRecord(
            call_id, rule, code, armed_on, entry_on, "invalid_ohlc", None, None
        )

    entry = float(future["open"].iloc[0]) * (1 + float(costs.slippage_pct))
    stop = entry - protocol.stop_atr * atr
    if stop <= 0 or stop >= entry:
        return QuickProfitRecord(
            call_id, rule, code, armed_on, entry_on, "invalid_geometry", None, None
        )
    target = entry + protocol.target_r * (entry - stop)
    entry_spec = EntrySpec(
        on=entry_on,
        fill=entry,
        at_open=True,
        future=tuple(
            PriceBar(
                on=dates[entry_pos + offset],
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
            )
            for offset, (_, bar) in enumerate(future.iterrows())
        ),
    )
    outcome = _quick_outcome(
        entry_spec,
        stop=stop,
        target=target,
        max_hold=protocol.max_hold_sessions,
        qty=qty,
        costs=costs,
    )
    if outcome is None:
        return QuickProfitRecord(
            call_id, rule, code, armed_on, entry_on, "awaiting_outcome_sessions", None, None
        )
    won, net_r = outcome
    return QuickProfitRecord(call_id, rule, code, armed_on, entry_on, "resolved", won, net_r)


def _resolved(records: list[QuickProfitRecord], rule: str) -> list[QuickProfitRecord]:
    return [
        row
        for row in records
        if row.rule == rule
        and row.status == "resolved"
        and row.strict_success is not None
        and row.net_r is not None
    ]


def _session_values(
    records: list[QuickProfitRecord], rule: str
) -> dict[date, tuple[float, float, int]]:
    grouped: dict[date, list[QuickProfitRecord]] = defaultdict(list)
    for row in _resolved(records, rule):
        grouped[row.armed_on].append(row)
    return {
        session: (
            float(np.mean([float(row.net_r) for row in rows])),
            float(np.mean([int(row.strict_success) for row in rows])),
            len(rows),
        )
        for session, rows in grouped.items()
    }


def one_sided_sign_flip_pvalue(
    differences: list[float], *, seed: int, draws: int
) -> float | None:
    """One-sided paired-session null test; unavailable evidence stays unavailable."""

    values = np.asarray(differences, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    observed = float(values.mean())
    if observed <= 0:
        return 1.0
    if len(values) <= 16:
        null = np.fromiter(
            (
                float(np.mean(values * np.asarray(signs, dtype=float)))
                for signs in itertools.product((-1.0, 1.0), repeat=len(values))
            ),
            dtype=float,
        )
    else:
        rng = np.random.default_rng(seed)
        signs = rng.choice((-1.0, 1.0), size=(draws, len(values)))
        null = (signs * values).mean(axis=1)
    return float((1 + np.count_nonzero(null >= observed - 1e-15)) / (len(null) + 1))


def holm_adjusted_pvalues(raw: dict[str, float | None]) -> dict[str, float | None]:
    """Holm familywise correction; missing trials do not shrink the registered family."""

    adjusted: dict[str, float | None] = {name: None for name in raw}
    ordered = sorted((p, name) for name, p in raw.items() if p is not None)
    family_size = len(raw)
    running = 0.0
    for rank, (pvalue, name) in enumerate(ordered):
        running = max(running, min(1.0, pvalue * (family_size - rank)))
        adjusted[name] = running
    return adjusted


def _cohort_rows(
    source_rows: list[dict[str, Any]], *, prospective: bool, protocol: BseQuickProfitProtocol
) -> list[dict[str, Any]]:
    output = []
    call_id_counts = Counter(
        str(row.get("call_id", "")) for row in source_rows if str(row.get("call_id", ""))
    )
    for row in source_rows:
        if not str(row.get("scrip_code", "")).startswith("BSE_"):
            continue
        if str(row.get("rule", "")) not in FROZEN_RULES:
            continue
        call_id = str(row.get("call_id", ""))
        if not call_id or call_id_counts[call_id] != 1:
            continue
        try:
            armed_on = date.fromisoformat(str(row["armed_on"]))
        except (KeyError, TypeError, ValueError):
            continue
        if prospective:
            if armed_on < protocol.activation_on:
                continue
            try:
                logged_at = datetime.fromisoformat(str(row["logged_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            # Conservative forward provenance: B1 accepts only calls durably logged on
            # their arming session.  A catch-up replay on a later date may already know
            # the next-session open and can never be relabeled as prospective evidence.
            logged_on = logged_at.astimezone(IST).date() if logged_at.tzinfo else logged_at.date()
            if logged_on != armed_on:
                continue
            output.append(row)
        elif armed_on < protocol.activation_on:
            output.append(row)
    return output


def _cohort_records(
    records: list[QuickProfitRecord], source_rows: list[dict[str, Any]]
) -> list[QuickProfitRecord]:
    source_counts = Counter(str(row.get("call_id", "")) for row in source_rows)
    record_counts = Counter(row.call_id for row in records)
    allowed = {
        call_id
        for call_id, count in source_counts.items()
        if call_id and count == 1 and record_counts[call_id] == 1
    }
    return [row for row in records if row.call_id in allowed]


def _trial_summary(
    rule: str,
    records: list[QuickProfitRecord],
    source_rows: list[dict[str, Any]],
    *,
    adjusted_pvalue: float | None,
    protocol: BseQuickProfitProtocol,
    development_only: bool,
) -> dict[str, Any]:
    candidate = _resolved(records, rule)
    candidate_sessions = _session_values(records, rule)
    control_sessions = _session_values(records, CONTROL_RULE)
    matched = sorted(set(candidate_sessions) & set(control_sessions))
    differences = [candidate_sessions[d][0] - control_sessions[d][0] for d in matched]
    control_success_differences = [
        candidate_sessions[d][1] - control_sessions[d][1] for d in matched
    ]
    issued = [row for row in source_rows if str(row.get("rule", "")) == rule]
    wins = sum(int(row.strict_success) for row in candidate)
    n = len(candidate)
    active_sessions = len(candidate_sessions)
    source_sessions = len(
        {str(row.get("armed_on")) for row in source_rows if row.get("armed_on")}
    )
    session_target_rate = (
        sum(values[1] >= protocol.min_observed_success for values in candidate_sessions.values())
        / active_sessions
        if active_sessions
        else 0.0
    )
    status_counts = Counter(row.status for row in records if row.rule == rule)
    observed_success = wins / n if n else None
    expectancy = float(np.mean([float(row.net_r) for row in candidate])) if n else None
    control_advantage = float(np.mean(differences)) if differences else None
    control_success_advantage = (
        float(np.mean(control_success_differences)) if control_success_differences else None
    )
    sample_failures = []
    if n < protocol.min_resolved_calls:
        sample_failures.append(f"resolved calls {n} < {protocol.min_resolved_calls}")
    if active_sessions < protocol.min_active_sessions:
        sample_failures.append(
            f"active sessions {active_sessions} < {protocol.min_active_sessions}"
        )
    if len(matched) < protocol.min_matched_control_sessions:
        sample_failures.append(
            "matched control sessions "
            f"{len(matched)} < {protocol.min_matched_control_sessions}"
        )
    coverage = active_sessions / source_sessions if source_sessions else 0.0
    if coverage < protocol.min_session_coverage:
        sample_failures.append(
            f"session coverage {coverage:.3f} < {protocol.min_session_coverage:.3f}"
        )

    evidence_failures = []
    if observed_success is None or observed_success < protocol.min_observed_success:
        evidence_failures.append(
            "observed strict success unavailable/below "
            f"{protocol.min_observed_success:.0%}"
        )
    wilson = wilson_lower_bound(wins, n) if n else None
    if wilson is None or wilson < protocol.min_wilson_lower:
        evidence_failures.append(
            f"Wilson lower bound unavailable/below {protocol.min_wilson_lower:.0%}"
        )
    if session_target_rate < protocol.min_session_target_rate:
        evidence_failures.append(
            "successful-session rate "
            f"{session_target_rate:.3f} < {protocol.min_session_target_rate:.3f}"
        )
    if expectancy is None or expectancy <= protocol.min_after_cost_expectancy_r:
        evidence_failures.append("after-cost expectancy unavailable/non-positive")
    if control_advantage is None or control_advantage < protocol.min_control_advantage_r:
        evidence_failures.append(
            "matched-control advantage unavailable/below "
            f"{protocol.min_control_advantage_r:+.2f}R"
        )
    if adjusted_pvalue is None or adjusted_pvalue > protocol.familywise_alpha:
        evidence_failures.append(
            "Holm-adjusted paired-session p unavailable/above "
            f"{protocol.familywise_alpha:.3f}"
        )

    if development_only:
        verdict = "development_only"
    elif sample_failures:
        verdict = "collecting"
    elif evidence_failures:
        verdict = "rejected"
    else:
        verdict = "qualified_research_only"
    return {
        "rule": rule,
        "verdict": verdict,
        "development_only": development_only,
        "issued": len(issued),
        "resolved": n,
        "unresolved_or_excluded": len(issued) - n,
        "status_counts": dict(sorted(status_counts.items())),
        "wins": wins,
        "observed_strict_success": observed_success,
        "wilson_lower_bound": wilson,
        "after_cost_expectancy_r": expectancy,
        "active_sessions": active_sessions,
        "source_sessions": source_sessions,
        "session_coverage": coverage,
        "sessions_meeting_80pct": sum(
            values[1] >= protocol.min_observed_success for values in candidate_sessions.values()
        ),
        "session_target_rate": session_target_rate,
        "matched_control_sessions": len(matched),
        "matched_control_advantage_r": control_advantage,
        "matched_control_success_advantage": control_success_advantage,
        "holm_adjusted_pvalue": adjusted_pvalue,
        "sample_ready": not sample_failures,
        "sample_failures": sample_failures,
        "evidence_failures": evidence_failures,
        "qualified": verdict == "qualified_research_only",
    }


def summarize_cohort(
    records: list[QuickProfitRecord],
    source_rows: list[dict[str, Any]],
    *,
    protocol: BseQuickProfitProtocol,
    development_only: bool,
) -> dict[str, Any]:
    candidate_session_values = {
        rule: _session_values(records, rule) for rule in CANDIDATE_RULES
    }
    control_sessions = _session_values(records, CONTROL_RULE)
    raw_pvalues: dict[str, float | None] = {}
    for offset, rule in enumerate(CANDIDATE_RULES):
        matched = sorted(set(candidate_session_values[rule]) & set(control_sessions))
        differences = [
            candidate_session_values[rule][day][0] - control_sessions[day][0]
            for day in matched
        ]
        raw_pvalues[rule] = one_sided_sign_flip_pvalue(
            differences,
            seed=protocol.random_seed + offset,
            draws=protocol.sign_flip_draws,
        )
    adjusted = holm_adjusted_pvalues(raw_pvalues)
    trials = [
        _trial_summary(
            rule,
            records,
            source_rows,
            adjusted_pvalue=adjusted[rule],
            protocol=protocol,
            development_only=development_only,
        )
        for rule in CANDIDATE_RULES
    ]
    control = _resolved(records, CONTROL_RULE)
    control_wins = sum(int(row.strict_success) for row in control)
    control_sessions_count = len(_session_values(records, CONTROL_RULE))
    return {
        "use": "transport_diagnostic_only" if development_only else "prospective_evidence",
        "source_rows": len(source_rows),
        "source_sessions": len(
            {str(row.get("armed_on")) for row in source_rows if row.get("armed_on")}
        ),
        "control": {
            "rule": CONTROL_RULE,
            "resolved": len(control),
            "wins": control_wins,
            "observed_strict_success": control_wins / len(control) if control else None,
            "after_cost_expectancy_r": (
                float(np.mean([float(row.net_r) for row in control])) if control else None
            ),
            "active_sessions": control_sessions_count,
        },
        "raw_pvalues": raw_pvalues,
        "trials": trials,
        "qualified_rules": [row["rule"] for row in trials if row["qualified"]],
        "rules_sample_ready": sum(bool(row["sample_ready"]) for row in trials),
    }


def _store_state(store: CandleStore) -> dict[str, Any]:
    row = store.con.execute(
        """
        SELECT count(*), count(DISTINCT scrip_code), min(ts), max(ts)
        FROM candles WHERE interval = ? AND scrip_code LIKE 'BSE_%'
        """,
        [Interval.D1.value],
    ).fetchone()
    if row is None:
        return {"daily_rows": 0, "symbols": 0, "first_session": None, "last_session": None}
    first = datetime.fromtimestamp(int(row[2]), tz=IST).date() if row[2] is not None else None
    last = datetime.fromtimestamp(int(row[3]), tz=IST).date() if row[3] is not None else None
    return {
        "daily_rows": int(row[0]),
        "symbols": int(row[1]),
        "first_session": first.isoformat() if first else None,
        "last_session": last.isoformat() if last else None,
    }


def run_bse_quick_profit(
    *,
    root: Path = ROOT,
    protocol: BseQuickProfitProtocol = DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
) -> dict[str, Any]:
    source_log = root / protocol.source_log
    source_db = root / protocol.source_db
    source_rows = _source_rows(source_log)
    settings = load_config(root)
    market = bse_market(settings)
    bars_cache: dict[str, pd.DataFrame | None] = {}
    records: list[QuickProfitRecord] = []
    with CandleStore(source_db) as store:
        store_state = _store_state(store)
        for row in source_rows:
            code = str(row.get("scrip_code", ""))
            if code not in bars_cache:
                bars_cache[code] = (
                    store.load(code, Interval.D1) if code.startswith("BSE_") else None
                )
            bars = bars_cache[code]
            qty = 0.0
            try:
                armed_on = date.fromisoformat(str(row["armed_on"]))
                atr = float(row["atr_at_arm"])
            except (KeyError, TypeError, ValueError):
                armed_on, atr = date.min, float("nan")
            if bars is not None and not bars.empty and armed_on != date.min and math.isfinite(atr):
                dates = _date_index(bars)
                next_positions = np.flatnonzero(dates > armed_on)
                if len(next_positions):
                    entry_pos = int(next_positions[0])
                    raw_open = float(bars["open"].iloc[entry_pos])
                    entry = raw_open * (1 + float(market.costs.slippage_pct))
                    stop = entry - protocol.stop_atr * atr
                    if math.isfinite(entry) and stop > 0 and stop < entry:
                        gap = gap95_pct(bars.iloc[:entry_pos])
                        qty = position_size(
                            SizeInputs(
                                equity=float(settings.risk.trading_capital),
                                entry=entry,
                                stop=stop,
                                max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
                                max_position_value_pct=float(
                                    settings.risk.max_position_value_pct
                                ),
                                size_multiplier=float(
                                    settings.risk.regime_size_multiplier.neutral
                                ),
                                gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
                                gap95_pct=gap,
                                available_heat_pct=float(settings.risk.max_portfolio_heat_pct),
                                qty_step=market.qty_step,
                                min_notional=market.min_notional_inr,
                            )
                        ).qty
            records.append(
                resolve_quick_profit_call(
                    row, bars, qty=qty, costs=market.costs, protocol=protocol
                )
            )

    development_rows = _cohort_rows(source_rows, prospective=False, protocol=protocol)
    prospective_rows = _cohort_rows(source_rows, prospective=True, protocol=protocol)
    development_records = _cohort_records(records, development_rows)
    prospective_records = _cohort_records(records, prospective_rows)
    development = summarize_cohort(
        development_records,
        development_rows,
        protocol=protocol,
        development_only=True,
    )
    prospective = summarize_cohort(
        prospective_records,
        prospective_rows,
        protocol=protocol,
        development_only=False,
    )
    qualified = prospective["qualified_rules"]
    prospective_ids = {str(row.get("call_id", "")) for row in prospective_rows}
    prospective_registration_exclusions = sum(
        str(row.get("scrip_code", "")).startswith("BSE_")
        and str(row.get("rule", "")) in FROZEN_RULES
        and _safe_date(row.get("armed_on")) >= protocol.activation_on
        and str(row.get("call_id", "")) not in prospective_ids
        for row in source_rows
    )
    all_trials_decided = all(
        row["verdict"] in {"rejected", "qualified_research_only"}
        for row in prospective["trials"]
    )
    status = (
        "research_qualified"
        if qualified
        else "rejected" if all_trials_decided else "collecting"
    )
    source_sha = digest(source_log) if source_log.exists() else None
    return {
        "schema_version": protocol.state_schema_version,
        "created_at": datetime.now(IST).isoformat(),
        "market": protocol.market,
        "status": status,
        "live": False,
        "baseline_improved": False,
        "promotion_allowed": False,
        "detail": (
            "prospective BSE evidence is still collecting; pre-activation rows are "
            "transport diagnostics only"
            if status == "collecting"
            else "research result remains non-live and requires an independent promotion review"
        ),
        "protocol": asdict(protocol) | {"sha256": protocol.sha256},
        "geometry": {
            "entry_mode": protocol.entry_mode,
            "stop_atr": protocol.stop_atr,
            "target_r": protocol.target_r,
            "max_hold_sessions": protocol.max_hold_sessions,
            "trial_count": 1,
        },
        "data_source": {
            "log": protocol.source_log,
            "log_sha256": source_sha,
            "database": protocol.source_db,
            "store": store_state,
            "rows_read": len(source_rows),
            "registered_bse_rows": sum(
                str(row.get("scrip_code", "")).startswith("BSE_")
                and str(row.get("rule", "")) in FROZEN_RULES
                for row in source_rows
            ),
            "prospective_registration_contract": "logged_on_arming_session",
            "prospective_registration_exclusions": prospective_registration_exclusions,
        },
        "readiness": {
            "activation_date": protocol.activation_date,
            "prospective_source_sessions": prospective["source_sessions"],
            "required_sessions": protocol.min_active_sessions,
            "rules_sample_ready": prospective["rules_sample_ready"],
            "registered_candidate_rules": len(CANDIDATE_RULES),
        },
        "development_transport": development,
        "prospective": prospective,
    }


def save_bse_quick_profit(
    result: dict[str, Any],
    output: Path,
    *,
    protocol: BseQuickProfitProtocol = DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
) -> Path:
    # Protect the registered first look even when an older caller still follows
    # the split run-then-save API.
    if _load_terminal_bse_quick_profit(output, protocol) is None:
        write_json(output, _stamp_terminal_result(result))
    return output


def refresh_bse_quick_profit(
    *,
    root: Path = ROOT,
    protocol: BseQuickProfitProtocol = DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
    output: Path = DEFAULT_EVIDENCE,
) -> dict[str, Any]:
    """Refresh B1 evidence once, latching its first terminal prospective look."""

    terminal = _load_terminal_bse_quick_profit(output, protocol)
    if terminal is not None:
        return terminal
    result = run_bse_quick_profit(root=root, protocol=protocol)
    save_bse_quick_profit(result, output, protocol=protocol)
    # Re-read after saving so a concurrent first terminal writer also wins.
    return _load_terminal_bse_quick_profit(output, protocol) or result
