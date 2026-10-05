"""Research-only crypto accuracy recovery through smaller quick-profit geometry.

The tracker backfill is used only to choose geometry.  Rows explicitly recorded as live
are reported as a separate retrospective validation cohort and are never used for
selection.  Nothing in this module can change C1/C2, the qualified baseline, or live
eligibility.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.markets import crypto_market
from tradedesk.markets.tax import after_tax_r, tds_share_of_costs
from tradedesk.models import TradeType, price_decimal
from tradedesk.prediction.labeling import triple_barrier
from tradedesk.reliability import wilson_lower_bound
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

VERSION = "crypto-accuracy-recovery-r3-v1"
DEFAULT_LOG = ROOT / "data/reports/crypto_signal_tracking.jsonl"
DEFAULT_DB = ROOT / "data/crypto.duckdb"
DEFAULT_OUTPUT = OUTPUT / "crypto_accuracy_recovery"
NOTIONAL_INR = 10_000.0


@dataclass(frozen=True)
class RecoveryCandidate:
    target_r: float
    max_hold_sessions: int

    @property
    def id(self) -> str:
        target = str(self.target_r).replace(".", "p")
        return f"target_{target}r_hold_{self.max_hold_sessions}d"


@dataclass(frozen=True)
class RecoveryContract:
    target_rs: tuple[float, ...] = (0.25, 0.50, 0.75, 1.00)
    max_hold_sessions: tuple[int, ...] = (1, 3, 5, 7)
    folds: int = 3
    initial_train_fraction: float = 0.40
    embargo_days: int = 7
    minimum_development_resolved: int = 100
    minimum_walk_forward_resolved: int = 60
    minimum_live_validation_resolved: int = 30
    minimum_accuracy: float = 0.50
    minimum_wilson95_lower: float = 0.40
    minimum_mean_net_r: float = 0.0
    minimum_positive_folds: int = 2

    def __post_init__(self) -> None:
        if self.folds < 2:
            raise ValueError("recovery requires at least two chronological folds")
        if not 0 < self.initial_train_fraction < 1:
            raise ValueError("initial_train_fraction must be between zero and one")
        if self.embargo_days < max(self.max_hold_sessions):
            raise ValueError("embargo must cover the longest registered outcome window")
        if any(value <= 0 or not math.isfinite(value) for value in self.target_rs):
            raise ValueError("target R values must be positive and finite")
        if any(value < 1 for value in self.max_hold_sessions):
            raise ValueError("maximum holding sessions must be positive")

    @property
    def candidates(self) -> tuple[RecoveryCandidate, ...]:
        return tuple(
            RecoveryCandidate(target_r=target, max_hold_sessions=hold)
            for target in self.target_rs
            for hold in self.max_hold_sessions
        )


DEFAULT_CONTRACT = RecoveryContract()


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _report_sha(report: dict[str, Any]) -> str:
    return _sha({key: value for key, value in report.items() if key != "report_sha256"})


def _read_tracker(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError("crypto tracker log is empty")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("crypto tracker log contains a non-object row")
    ids = [str(row.get("signal_id") or "") for row in rows]
    if any(not value for value in ids):
        raise ValueError("crypto tracker log contains a missing signal ID")
    if len(ids) != len(set(ids)):
        raise ValueError("crypto tracker log contains duplicate signal IDs")
    return rows


def _replay_one(
    row: dict[str, Any],
    bars: pd.DataFrame,
    candidate: RecoveryCandidate,
    *,
    costs: Any,
    tds_share: float,
) -> dict[str, Any]:
    base = {
        "signal_id": str(row["signal_id"]),
        "scrip_code": str(row["scrip_code"]),
        "symbol": str(row.get("symbol") or row["scrip_code"]),
        "setup": str(row["setup"]),
        "armed_on": str(row["armed_on"]),
        "source": str(row.get("source") or "backfill"),
        "shadow": bool(row.get("shadow", False)),
        "candidate_id": candidate.id,
        "target_r": candidate.target_r,
        "max_hold_sessions": candidate.max_hold_sessions,
        "status": "excluded",
        "exclusion_reason": None,
        "outcome": None,
        "label": None,
        "gross_r": None,
        "net_r": None,
        "after_tax_r": None,
    }
    try:
        armed = date.fromisoformat(str(row["armed_on"]))
        trigger = float(row["entry"])
        stop = float(row["stop"])
    except (KeyError, TypeError, ValueError):
        base["exclusion_reason"] = "invalid_tracker_geometry"
        return base
    if bars.empty or not (0 < stop < trigger):
        base["exclusion_reason"] = "missing_bars_or_invalid_risk"
        return base
    dates = pd.DatetimeIndex(bars.index).date
    entry_index = next((index for index, value in enumerate(dates) if value > armed), None)
    if entry_index is None:
        base.update(status="pending", exclusion_reason="right_censored")
        return base
    slip = float(costs.slippage_pct)
    fill = trigger * (1 + slip)
    risk_per_unit = fill - stop
    target = fill + candidate.target_r * risk_per_unit
    if not (0 < stop < fill < target):
        base["exclusion_reason"] = "invalid_cost_adjusted_geometry"
        return base
    label = triple_barrier(
        bars.iloc[entry_index:],
        entry=fill,
        stop=stop,
        target=target,
        max_hold=candidate.max_hold_sessions - 1,
    )
    if label.outcome == "insufficient" or label.exit_price is None:
        base.update(status="pending", exclusion_reason="right_censored")
        return base
    exit_fill = float(label.exit_price) * (1 - slip)
    qty = NOTIONAL_INR / fill
    round_trip = costs.round_trip_cost(
        trade_type=TradeType.DELIVERY,
        qty=qty,
        entry_price=price_decimal(fill),
        exit_price=price_decimal(exit_fill),
    )
    gross_pnl = (exit_fill - fill) * qty
    net_pnl = gross_pnl - float(round_trip.total)
    risk = risk_per_unit * qty
    gross_r = gross_pnl / risk
    net_r = net_pnl / risk
    taxed_r = after_tax_r(SimpleNamespace(gross_r=gross_r, net_r=net_r), tds_share)
    base.update(
        status="resolved",
        exclusion_reason=None,
        outcome=label.outcome,
        label=int(label.outcome == "target" and net_pnl > 0),
        gross_r=gross_r,
        net_r=net_r,
        after_tax_r=taxed_r,
    )
    return base


def build_replay(
    records: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB,
    contract: RecoveryContract = DEFAULT_CONTRACT,
) -> pd.DataFrame:
    """Re-label tracker calls for the frozen quick-profit grid using closed daily bars."""

    settings = load_config(ROOT)
    market = crypto_market(settings)
    schedule = market.costs.schedule
    tds_share = tds_share_of_costs(
        maker_taker_pct=float(schedule.maker_taker_pct),
        tds_pct=float(schedule.tds_pct),
        gst_pct=float(schedule.gst_pct),
        slippage_pct=float(schedule.slippage_pct),
    )
    codes = sorted({str(row["scrip_code"]) for row in records})
    bars_by_code: dict[str, pd.DataFrame] = {}
    with CandleStore(db_path) as store:
        for code in codes:
            bars_by_code[code] = store.load(code, Interval.D1, adjusted=False)
    rows = [
        _replay_one(
            record,
            bars_by_code.get(str(record["scrip_code"]), pd.DataFrame()),
            candidate,
            costs=market.costs,
            tds_share=tds_share,
        )
        for record in records
        for candidate in contract.candidates
    ]
    return pd.DataFrame(rows).sort_values(
        ["setup", "armed_on", "signal_id", "candidate_id"]
    ).reset_index(drop=True)


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    resolved = frame.loc[frame.status.eq("resolved")]
    n = len(resolved)
    wins = int(resolved.label.sum()) if n else 0
    return {
        "resolved_calls": n,
        "wins": wins,
        "active_sessions": int(resolved.armed_on.nunique()) if n else 0,
        "observed_accuracy": float(wins / n) if n else None,
        "wilson95_lower": float(wilson_lower_bound(wins, n)) if n else None,
        "mean_gross_r": float(resolved.gross_r.mean()) if n else None,
        "mean_net_r": float(resolved.net_r.mean()) if n else None,
        "mean_after_tax_r": float(resolved.after_tax_r.mean()) if n else None,
    }


def _rank(metrics: dict[str, Any]) -> tuple[float, ...]:
    net = metrics["mean_net_r"]
    taxed = metrics["mean_after_tax_r"]
    return (
        float(net is not None and net > 0 and taxed is not None and taxed > 0),
        float(metrics["wilson95_lower"] or -1.0),
        float(taxed if taxed is not None else -1e9),
        float(net if net is not None else -1e9),
        float(metrics["observed_accuracy"] or -1.0),
    )


def _candidate_table(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate_id, candidate_frame in frame.groupby("candidate_id", sort=True):
        metrics = _metrics(candidate_frame)
        first = candidate_frame.iloc[0]
        rows.append(
            {
                "candidate_id": str(candidate_id),
                "target_r": float(first.target_r),
                "max_hold_sessions": int(first.max_hold_sessions),
                "metrics": metrics,
            }
        )
    return sorted(rows, key=lambda row: (_rank(row["metrics"]), row["candidate_id"]), reverse=True)


def _walk_forward(
    development: pd.DataFrame,
    contract: RecoveryContract,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    sessions = sorted({date.fromisoformat(value) for value in development.armed_on})
    if len(sessions) < contract.folds + 2:
        return [], development.iloc[0:0].copy()
    first_test = max(1, int(math.ceil(len(sessions) * contract.initial_train_fraction)))
    test_sessions = sessions[first_test:]
    blocks = [list(block) for block in _array_split(test_sessions, contract.folds) if block]
    reports: list[dict[str, Any]] = []
    selected_rows: list[pd.DataFrame] = []
    armed_dates = development.armed_on.map(date.fromisoformat)
    for fold_number, block in enumerate(blocks, start=1):
        test_start = block[0]
        cutoff = test_start - timedelta(days=contract.embargo_days)
        train = development.loc[armed_dates < cutoff]
        trials = _candidate_table(train)
        if not trials:
            continue
        nominee = trials[0]
        test_mask = armed_dates.isin(block) & development.candidate_id.eq(
            nominee["candidate_id"]
        )
        test = development.loc[test_mask].copy()
        metrics = _metrics(test)
        selected_rows.append(test)
        reports.append(
            {
                "fold": fold_number,
                "train_through": (cutoff - timedelta(days=1)).isoformat(),
                "test_first_session": block[0].isoformat(),
                "test_last_session": block[-1].isoformat(),
                "selected_candidate": nominee["candidate_id"],
                "train_metrics": nominee["metrics"],
                "test_metrics": metrics,
                "positive": bool(
                    metrics["observed_accuracy"] is not None
                    and metrics["observed_accuracy"] >= contract.minimum_accuracy
                    and metrics["mean_net_r"] is not None
                    and metrics["mean_net_r"] > contract.minimum_mean_net_r
                    and metrics["mean_after_tax_r"] is not None
                    and metrics["mean_after_tax_r"] > contract.minimum_mean_net_r
                ),
            }
        )
    combined = (
        pd.concat(selected_rows, ignore_index=True)
        if selected_rows
        else development.iloc[0:0].copy()
    )
    return reports, combined


def _array_split(values: list[date], parts: int) -> list[list[date]]:
    """Deterministic chronological equivalent of numpy.array_split without numpy objects."""

    quotient, remainder = divmod(len(values), parts)
    blocks: list[list[date]] = []
    cursor = 0
    for index in range(parts):
        width = quotient + (1 if index < remainder else 0)
        blocks.append(values[cursor : cursor + width])
        cursor += width
    return blocks


def _setup_report(
    replay: pd.DataFrame,
    setup: str,
    contract: RecoveryContract,
) -> dict[str, Any]:
    setup_frame = replay.loc[replay.setup.eq(setup)].copy()
    development = setup_frame.loc[setup_frame.source.eq("backfill")]
    live = setup_frame.loc[setup_frame.source.eq("live")]
    trials = _candidate_table(development)
    nominee = trials[0] if trials else None
    folds, selected_oos = _walk_forward(development, contract)
    walk_forward = _metrics(selected_oos)
    live_validation = (
        _metrics(live.loc[live.candidate_id.eq(nominee["candidate_id"])])
        if nominee is not None
        else _metrics(live.iloc[0:0])
    )
    development_metrics = nominee["metrics"] if nominee else _metrics(development.iloc[0:0])
    positive_folds = sum(bool(fold["positive"]) for fold in folds)
    gates = {
        "development_sample": development_metrics["resolved_calls"]
        >= contract.minimum_development_resolved,
        "development_accuracy": development_metrics["observed_accuracy"] is not None
        and development_metrics["observed_accuracy"] >= contract.minimum_accuracy,
        "development_wilson": development_metrics["wilson95_lower"] is not None
        and development_metrics["wilson95_lower"] >= contract.minimum_wilson95_lower,
        "development_positive_net_r": development_metrics["mean_net_r"] is not None
        and development_metrics["mean_net_r"] > contract.minimum_mean_net_r,
        "development_positive_after_tax_r": development_metrics["mean_after_tax_r"]
        is not None
        and development_metrics["mean_after_tax_r"] > contract.minimum_mean_net_r,
        "walk_forward_sample": walk_forward["resolved_calls"]
        >= contract.minimum_walk_forward_resolved,
        "walk_forward_accuracy": walk_forward["observed_accuracy"] is not None
        and walk_forward["observed_accuracy"] >= contract.minimum_accuracy,
        "walk_forward_positive_net_r": walk_forward["mean_net_r"] is not None
        and walk_forward["mean_net_r"] > contract.minimum_mean_net_r,
        "walk_forward_positive_after_tax_r": walk_forward["mean_after_tax_r"]
        is not None
        and walk_forward["mean_after_tax_r"] > contract.minimum_mean_net_r,
        "walk_forward_positive_folds": positive_folds >= contract.minimum_positive_folds,
        "live_validation_sample": live_validation["resolved_calls"]
        >= contract.minimum_live_validation_resolved,
        "live_validation_accuracy": live_validation["observed_accuracy"] is not None
        and live_validation["observed_accuracy"] >= contract.minimum_accuracy,
        "live_validation_wilson": live_validation["wilson95_lower"] is not None
        and live_validation["wilson95_lower"] >= contract.minimum_wilson95_lower,
        "live_validation_positive_net_r": live_validation["mean_net_r"] is not None
        and live_validation["mean_net_r"] > contract.minimum_mean_net_r,
        "live_validation_positive_after_tax_r": live_validation["mean_after_tax_r"]
        is not None
        and live_validation["mean_after_tax_r"] > contract.minimum_mean_net_r,
    }
    passed = bool(nominee is not None and all(gates.values()))
    return {
        "setup": setup,
        "status": "shadow_candidate_ready_research_only" if passed else "rejected_or_insufficient",
        "registered_candidates": len(trials),
        "development_source": "tracker rows explicitly marked backfill",
        "validation_source": "tracker rows explicitly marked live; retrospective only",
        "nominee": (
            {
                "candidate_id": nominee["candidate_id"],
                "target_r": nominee["target_r"],
                "max_hold_sessions": nominee["max_hold_sessions"],
            }
            if nominee
            else None
        ),
        "development": development_metrics,
        "walk_forward": {
            **walk_forward,
            "folds": folds,
            "positive_folds": positive_folds,
        },
        "live_validation": live_validation,
        "gates": gates,
        "passed_research_gate": passed,
        "trials": trials,
    }


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    escaped = str(temporary).replace("'", "''")
    with duckdb.connect(":memory:") as con:
        con.register("_replay", frame)
        con.execute(f"COPY _replay TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    temporary.replace(path)


def run_crypto_accuracy_recovery(
    *,
    log_path: Path = DEFAULT_LOG,
    db_path: Path = DEFAULT_DB,
    output: Path = DEFAULT_OUTPUT,
    contract: RecoveryContract = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    """Run the bounded geometry race and persist an immutable research-only report."""

    log_path, db_path, output = Path(log_path), Path(db_path), Path(output)
    records = _read_tracker(log_path)
    replay = build_replay(records, db_path=db_path, contract=contract)
    setups = sorted(replay.setup.unique())
    setup_reports = [_setup_report(replay, setup, contract) for setup in setups]
    passed = [row for row in setup_reports if row["passed_research_gate"]]
    diagnostic = max(
        setup_reports,
        key=lambda row: (
            bool(row["passed_research_gate"]),
            _rank(row["walk_forward"]),
            _rank(row["development"]),
            row["setup"],
        ),
    )
    # Persist the same JSON-native structure that callers and the verifier read back.
    contract_payload = json.loads(_canonical(asdict(contract)))
    contract_sha = _sha(contract_payload)
    implementation_sha = digest(Path(__file__))
    tracker_sha = digest(log_path)
    created_at = datetime.now(UTC).isoformat()
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{tracker_sha[:8]}-{contract_sha[:8]}"
    run_dir = output / "runs" / run_id
    replay_path = run_dir / "replay.parquet"
    trials_path = run_dir / "trials.json"
    _write_parquet(replay, replay_path)
    write_json(trials_path, setup_reports)
    report: dict[str, Any] = {
        "version": VERSION,
        "id": run_id,
        "created_at": created_at,
        "status": (
            "shadow_candidate_ready_research_only"
            if passed
            else "no_candidate_cleared_recovery_gates"
        ),
        "contract": contract_payload,
        "contract_sha256": contract_sha,
        "implementation": {"path": str(Path(__file__)), "sha256": implementation_sha},
        "source": {
            "tracker_path": str(log_path),
            "tracker_sha256": tracker_sha,
            "tracker_rows": len(records),
            "backfill_rows": sum(
                str(row.get("source") or "backfill") == "backfill" for row in records
            ),
            "live_rows": sum(str(row.get("source") or "backfill") == "live" for row in records),
            "database_path": str(db_path),
        },
        "trial_counts": {
            "setups": len(setup_reports),
            "registered_per_setup": len(contract.candidates),
            "registered_total": len(setup_reports) * len(contract.candidates),
            "passing_setups": len(passed),
        },
        "best_diagnostic": {
            key: diagnostic[key]
            for key in (
                "setup",
                "status",
                "nominee",
                "development",
                "walk_forward",
                "live_validation",
                "gates",
                "passed_research_gate",
            )
        },
        "setups": setup_reports,
        "artifacts": {
            "replay": {
                "path": str(replay_path),
                "rows": len(replay),
                "sha256": digest(replay_path),
            },
            "trials": {
                "path": str(trials_path),
                "sha256": digest(trials_path),
            },
        },
        "source_integrity": {"passed": True, "errors": []},
        "research_gate_passed": bool(passed),
        "baseline_improved": False,
        "eligible_for_live": False,
        "detail": (
            "Historical geometry research is separate from C1/C2. A research pass may "
            "authorize a new prospective shadow cohort only; it never changes the crypto "
            "baseline or live eligibility."
        ),
    }
    report["report_sha256"] = _report_sha(report)
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, report)
    write_json(output / "state.json", report)
    return report


def verify_crypto_accuracy_recovery(output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Verify latest R3 state and immutable artifacts, failing closed on every mismatch."""

    output = Path(output)
    try:
        report = json.loads((output / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("crypto recovery state is unreadable") from exc
    if not isinstance(report, dict):
        raise ValueError("crypto recovery state is not an object")
    errors: list[str] = []
    if report.get("version") != VERSION:
        errors.append("version")
    if report.get("report_sha256") != _report_sha(report):
        errors.append("report_sha256")
    if report.get("contract_sha256") != _sha(report.get("contract")):
        errors.append("contract_sha256")
    if report.get("baseline_improved") is not False:
        errors.append("baseline_improved")
    if report.get("eligible_for_live") is not False:
        errors.append("eligible_for_live")
    if (report.get("source_integrity") or {}).get("passed") is not True:
        errors.append("source_integrity")
    implementation = report.get("implementation")
    if not isinstance(implementation, dict):
        errors.append("implementation")
    else:
        try:
            implementation_sha = digest(Path(str(implementation.get("path") or "")))
        except OSError:
            implementation_sha = None
        if implementation_sha != implementation.get("sha256"):
            errors.append("implementation_sha256")
    source = report.get("source")
    if not isinstance(source, dict):
        errors.append("source")
    else:
        try:
            tracker_sha = digest(Path(str(source.get("tracker_path") or "")))
        except OSError:
            tracker_sha = None
        if tracker_sha != source.get("tracker_sha256"):
            errors.append("tracker_sha256")
    run_id = str(report.get("id") or "")
    try:
        immutable = json.loads(
            (output / "runs" / run_id / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        errors.append("manifest")
    else:
        if _canonical(immutable) != _canonical(report):
            errors.append("manifest_identity")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append("artifacts")
    else:
        for name in ("replay", "trials"):
            item = artifacts.get(name)
            if not isinstance(item, dict):
                errors.append(f"{name}_artifact")
                continue
            path = Path(str(item.get("path") or ""))
            try:
                observed = digest(path)
            except OSError:
                observed = None
            if observed != item.get("sha256"):
                errors.append(f"{name}_sha256")
    if errors:
        raise ValueError(
            "crypto accuracy recovery failed verification: " + ", ".join(errors)
        )
    return report


__all__ = [
    "DEFAULT_CONTRACT",
    "DEFAULT_DB",
    "DEFAULT_LOG",
    "DEFAULT_OUTPUT",
    "RecoveryCandidate",
    "RecoveryContract",
    "build_replay",
    "run_crypto_accuracy_recovery",
    "verify_crypto_accuracy_recovery",
]
