"""Conditional AEM random-policy diagnostic using the unchanged AEM execution engine.

This does not test stock/day selection or portfolio returns. Every sampled decision
uses only closed-bar information; only the intraday signal gates are bypassed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd

from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features, intraday_features
from tradedesk.markets.market import nse_market
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_data import read_aem_source, regular_minutes
from tradedesk_lab.aem_dataset import _complete_signal_session
from tradedesk_lab.aem_detector import (
    AemDecision,
    detect_daily_candidate,
    evaluate_trigger,
)
from tradedesk_lab.aem_features import intraday_snapshot
from tradedesk_lab.aem_labels import label_trade
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.mcb_features import time_of_day_rvol

KNOWN_STATUSES = {"resolved", "chased", "unsizeable", "no_pullback_fill"}


def decision_times(on: str | date, contract: AemContract) -> pd.DatetimeIndex:
    """The complete decision window, never filtered on future execution outcomes."""
    day = pd.Timestamp(on).date()
    return pd.date_range(
        f"{day} {contract.earliest_decision_time}",
        f"{day} {contract.latest_decision_time}",
        freq="min",
        tz="Asia/Kolkata",
    )


def null_decision(candidate, session, at, contract) -> AemDecision:
    if candidate.contract_sha256 != contract.sha256:
        raise ValueError("candidate and null contracts differ")
    values = intraday_snapshot(session, at=at, resistance=candidate.resistance, contract=contract)
    if not values["checks"]["decision_window"]:
        raise ValueError("null decision outside the frozen window")
    # Pattern may be None: label_trade then uses its normal market-entry branch.
    # Do not borrow the later observed trigger's pattern or its VWAP/price.
    return AemDecision(
        candidate.identifier,
        "TRADE",
        values["available_at"],
        float(values["signal_price"]),
        ("random_policy_bypasses_signal_gates",),
        values,
        contract.sha256,
    )


def counterfactual_grid(candidate, session, *, risk, costs, contract) -> pd.DataFrame:
    if not _complete_signal_session(session):
        raise ValueError("incomplete_counterfactual_session")
    on = session.index[0].date()
    rows = []
    for slot, at in enumerate(decision_times(on, contract)):
        decision = null_decision(candidate, session, at, contract)
        outcome = label_trade(candidate, decision, session, risk, costs, contract)
        rows.append(
            {
                "slot": slot,
                "available_at": decision.available_at,
                "signal_price": decision.signal_price,
                "signal_vwap": decision.features["vwap"],
                "signal_pattern": decision.features["entry_pattern"],
                **outcome,
            }
        )
    return pd.DataFrame(rows)


def sample_cohorts(
    grid: pd.DataFrame, *, n_cohorts: int, seed: int, expected_slot_count: int = 101
) -> pd.DataFrame:
    """Use a common random minute within each date to retain cross-stock co-timing."""
    if not 1 <= n_cohorts <= 5000 or not 0 <= seed < 2**32:
        raise ValueError("cohorts must be 1..5000 and seed must be uint32")
    required = {"event_id", "session_date", "slot", "status"}
    if grid.empty or not required.issubset(grid):
        raise ValueError("empty or incomplete counterfactual grid")
    if grid[list(required)].isna().any().any() or grid.duplicated(["event_id", "slot"]).any():
        raise ValueError("invalid or duplicate counterfactual identifiers")
    if not grid.status.isin(KNOWN_STATUSES).all():
        raise ValueError("unresolved_counterfactual_grid")
    groups = list(grid.groupby("event_id", sort=True))
    if not 1 <= expected_slot_count <= 375:
        raise ValueError("invalid frozen slot count")
    slots = list(range(expected_slot_count))
    if any(sorted(part.slot.tolist()) != slots for _, part in groups):
        raise ValueError("counterfactual grid must contain every slot for every attempt")
    if any(part.session_date.nunique() != 1 for _, part in groups):
        raise ValueError("event spans multiple sessions")
    index = grid.sort_values(["event_id", "slot"]).set_index(["event_id", "slot"])
    event_ids = [event for event, _ in groups]
    event_dates = [str(part.session_date.iloc[0]) for _, part in groups]
    dates = sorted(set(event_dates))
    date_position = {day: position for position, day in enumerate(dates)}
    rng = np.random.default_rng(seed)
    selected = rng.integers(0, len(slots), size=(n_cohorts, len(dates)))
    samples = []
    for cohort in range(n_cohorts):
        lookup = [
            (event, int(selected[cohort, date_position[day]]))
            for event, day in zip(event_ids, event_dates, strict=True)
        ]
        chosen = index.loc[lookup].reset_index()
        chosen.insert(0, "cohort_id", cohort)
        samples.append(chosen)
    return pd.concat(samples, ignore_index=True)


def _enrich(frame: pd.DataFrame, contract: AemContract) -> pd.DataFrame:
    """Mirror the frozen builder's cleaning; observed replay below checks parity."""
    raw = regular_minutes(frame.sort_index())
    numeric = raw[["open", "high", "low", "close", "volume"]]
    prices = raw[["open", "high", "low", "close"]]
    valid = (
        np.isfinite(numeric).all(axis=1)
        & (prices > 0).all(axis=1)
        & (raw.volume >= 0)
        & (raw.high >= prices.max(axis=1))
        & (raw.low <= prices.min(axis=1))
    )
    bad_dates = set(pd.DatetimeIndex(raw.index[~valid]).date)
    raw = raw.loc[~pd.Index(pd.DatetimeIndex(raw.index).date).isin(bad_dates)]
    result = intraday_features(raw)
    result["tod_rvol"] = time_of_day_rvol(
        result,
        lookback_sessions=contract.rvol_lookback_sessions,
        min_sessions=contract.rvol_min_sessions,
    )
    return result


def _assert_replay(saved: pd.Series, replay: dict) -> None:
    """Fail closed on changed execution results instead of mixing old and new labels."""
    for key, value in replay.items():
        if value is None:
            if key in saved and pd.notna(saved[key]):
                raise ValueError(f"observed_replay_mismatch:{saved.event_id}:{key}")
            continue
        if key not in saved or pd.isna(saved[key]):
            raise ValueError(f"observed_replay_missing:{saved.event_id}:{key}")
        original = saved[key]
        if isinstance(value, (bool, np.bool_)):
            equal = isinstance(original, (bool, np.bool_)) and bool(original) == bool(value)
        elif isinstance(value, (float, int, np.number)):
            equal = bool(np.isclose(float(original), float(value), rtol=1e-10, atol=1e-10))
        else:
            equal = str(original) == str(value)
        if not equal:
            raise ValueError(f"observed_replay_mismatch:{saved.event_id}:{key}")


def build_grid(events, daily, minute, *, risk, costs, contract):
    actual = events.loc[events.decision == "TRADE"].copy()
    if actual.empty:
        raise ValueError("no_observed_trade_attempts")
    if actual.event_id.isna().any() or actual.event_id.duplicated().any():
        raise ValueError("duplicate_or_missing_observed_event_id")
    actual = actual.sort_values(["session_date", "event_id"]).reset_index(drop=True)
    grids = []
    for code, subset in actual.groupby("scrip_code", sort=True):
        print(f"AEM null: {code}, {len(subset)} observed attempts", flush=True)
        if code not in daily or code not in minute:
            raise ValueError(f"missing_observed_source:{code}")
        days = daily_features(daily[code].sort_index())
        enriched = _enrich(minute[code], contract)
        sessions = {str(day): part for day, part in enriched.groupby(enriched.index.date)}
        for _, saved in subset.iterrows():
            candidate = detect_daily_candidate(code, saved.symbol, days, saved.armed_on, contract)
            if candidate is None or candidate.identifier != saved.candidate_id:
                raise ValueError(f"observed_candidate_mismatch:{saved.event_id}")
            session = sessions.get(str(saved.session_date))
            if session is None:
                raise ValueError(f"missing_observed_session:{saved.event_id}")
            decision = evaluate_trigger(
                candidate, session, pd.Timestamp(saved.available_at), contract
            )
            if decision.decision != "TRADE":
                raise ValueError(f"observed_trigger_mismatch:{saved.event_id}")
            _assert_replay(saved, label_trade(candidate, decision, session, risk, costs, contract))
            grid = counterfactual_grid(
                candidate, session, risk=risk, costs=costs, contract=contract
            )
            grid.insert(0, "session_date", str(saved.session_date))
            grid.insert(0, "event_id", saved.event_id)
            grid.insert(2, "scrip_code", code)
            grids.append(grid)
    return actual, pd.concat(grids, ignore_index=True)


def run_benchmark(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_id: str | None = None,
    n_cohorts: int = 500,
    seed: int = 20260923,
) -> dict:
    if not 1 <= n_cohorts <= 5000 or not 0 <= seed < 2**32:
        raise ValueError("cohorts must be 1..5000 and seed must be uint32")
    report_id = uuid4().hex
    target = output / "aem_benchmark/runs" / report_id
    target.mkdir(parents=True, exist_ok=False)
    report = {
        "id": report_id,
        "created_at": datetime.now(UTC).isoformat(),
        "artifact_path": str(target / "report.json"),
        "dataset_id": dataset_id,
        "status": "conditional_development_diagnostic",
        "eligible_for_live": False,
        "seed": seed,
        "cohorts_requested": n_cohorts,
        "sampling": "Uniform decision minute; one common draw per session per cohort.",
        "conditioning": "Observed TRADE stock-days including known unfilled attempts.",
        "entry_policy": "Closed-bar pattern/VWAP/price recomputed; only signal gates bypassed.",
        "evidence_class": "previously_inspected_development_sample",
    }
    try:
        if dataset_id is None:
            dataset_id = json.loads((output / "aem/latest.json").read_text())["id"]
        if not isinstance(dataset_id, str) or not re.fullmatch(r"[0-9a-f]{32}", dataset_id):
            raise ValueError("dataset id must be a 32-character lowercase hex id")
        report["dataset_id"] = dataset_id
        dataset_dir = output / "aem/datasets" / dataset_id
        manifest = json.loads((dataset_dir / "manifest.json").read_text())
        report["source_manifest_sha256"] = digest(dataset_dir / "manifest.json")
        report["source_events_csv_sha256"] = digest(dataset_dir / "events.csv")
        events = pd.read_csv(dataset_dir / "events.csv", float_precision="round_trip")
        actual_hash = hashlib.sha256(
            pd.util.hash_pandas_object(events, index=True).values.tobytes()
        ).hexdigest()
        if actual_hash != manifest["dataset_sha256"] or len(events) != manifest["events"]:
            raise ValueError("dataset_event_population_hash_mismatch")
        contract = AemContract(**manifest["contract"])
        if manifest["id"] != dataset_id or manifest["contract_sha256"] != contract.sha256:
            raise ValueError("dataset_contract_mismatch")
        for name, expected in manifest["dependency_sha256"].items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()) or digest(path) != expected:
                raise ValueError(f"dataset_dependency_changed:{name}")
        settings = load_config(root)
        daily, minute, _, _, _, source = read_aem_source(
            root,
            contract=contract,
            sessions=manifest["sessions_requested"],
            as_of=date.fromisoformat(manifest["source"]["as_of"]),
            benchmark_symbol=settings.universe.benchmark,
        )
        if source["sha256"] != manifest["source"]["sha256"]:
            raise ValueError("dataset_source_changed_rebuild_before_comparison")
        actual, grid = build_grid(
            events,
            daily,
            minute,
            risk=settings.risk,
            costs=nse_market(settings).costs,
            contract=contract,
        )
        grid.to_csv(target / "counterfactual_grid.csv", index=False)
        report["grid_status_counts"] = {
            str(k): int(v) for k, v in grid.status.value_counts().items()
        }
        report["grid_rows"] = len(grid)
        # Any incomplete path blocks the comparison, even if no random draw selected it.
        cohorts = sample_cohorts(
            grid,
            n_cohorts=n_cohorts,
            seed=seed,
            expected_slot_count=len(decision_times(actual.session_date.iloc[0], contract)),
        )
        from tradedesk_lab.aem_benchmark_stats import summarize_timing_null

        report["comparison"] = summarize_timing_null(actual, cohorts)
        cohorts.to_csv(target / "cohorts.csv", index=False)
        report["artifacts_sha256"] = {
            name: digest(target / name) for name in ("counterfactual_grid.csv", "cohorts.csv")
        }
        report["code_sha256"] = {
            name: digest(root / name)
            for name in (
                "tradedesk_lab/aem_benchmark.py",
                "tradedesk_lab/aem_benchmark_stats.py",
            )
        }
        report["contract_sha256"] = contract.sha256
        report["observed_replay_verified"] = len(actual)
    except (ValueError, OSError, KeyError, TypeError, duckdb.Error) as error:
        report.update(status="blocked_invalid_or_unavailable_source", error=str(error))
    report["limitations"] = [
        "Conditional stock/day selection is held fixed; this does not test the daily scanner.",
        "This compares intraday timing/gating/entry-style policy, not pure timing alone.",
        "History was repeatedly inspected; empirical tails are not confirmatory p-values.",
        "No concurrent capital, heat, sector or daily portfolio limits are simulated.",
        "Common session draws retain some cross-stock co-timing, not all market dependence.",
        "No whole-NSE, prospective, 70-80% reliability or live-eligibility claim is established.",
    ]
    write_json(target / "report.json", report)
    return report
