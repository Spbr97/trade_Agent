"""Registered validation gates for one frozen staged AEM dataset.

The runner never changes the signal contract or selects a better subset.  It replays
the exact observed TRADE population, compares it with same-stock/day random decision
minutes, applies the preregistered execution stresses, and runs filled calls through
the existing portfolio limits.  All outputs remain historical diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import yaml
from joblib import Parallel, delayed

from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features
from tradedesk.markets.market import nse_market
from tradedesk.models import Side, TradeType, price_decimal
from tradedesk.risk.sizing import SizeInputs, position_size
from tradedesk_lab.aem_benchmark import (
    _assert_replay,
    _enrich,
    counterfactual_grid,
    decision_times,
    sample_cohorts,
)
from tradedesk_lab.aem_benchmark_stats import summarize_timing_null
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_detector import detect_daily_candidate, evaluate_trigger
from tradedesk_lab.aem_labels import label_trade
from tradedesk_lab.aem_scorecard import build_accuracy_scorecard
from tradedesk_lab.aem_staged_data import read_staged_aem_source
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

EXECUTION_DEPENDENCIES = frozenset(
    {
        "tradedesk_lab/aem_contract.py",
        "tradedesk_lab/aem_dataset.py",
        "tradedesk_lab/aem_detector.py",
        "tradedesk_lab/aem_features.py",
        "tradedesk_lab/aem_labels.py",
        "tradedesk_lab/aem_staged_data.py",
        "tradedesk_lab/mcb_features.py",
        "src/tradedesk/engine/indicators.py",
        "src/tradedesk/risk/costs.py",
        "src/tradedesk/risk/sizing.py",
        "src/tradedesk/markets/costs.py",
        "src/tradedesk/markets/market.py",
        "config/risk.yaml",
        "config/universe.yaml",
    }
)


@dataclass(frozen=True)
class _CostStress:
    """CostModel view used only by label_trade; charges and slippage scale separately."""

    base: object
    charge_multiplier: Decimal = Decimal("1")
    slippage_multiplier: Decimal = Decimal("1")

    @property
    def slippage_pct(self) -> Decimal:
        return self.base.slippage_pct * self.slippage_multiplier

    def leg_cost(self, **kwargs):
        leg = self.base.leg_cost(**kwargs)
        return leg.model_copy(update={"total": leg.total * self.charge_multiplier})


def _hash_events(events: pd.DataFrame) -> str:
    return hashlib.sha256(
        pd.util.hash_pandas_object(events, index=True).values.tobytes()
    ).hexdigest()


def _load_dataset(root: Path, output: Path, dataset_id: str | None):
    if dataset_id is None:
        dataset_id = json.loads((output / "aem_staged/latest.json").read_text())["id"]
    if not isinstance(dataset_id, str) or not re.fullmatch(r"[0-9a-f]{32}", dataset_id):
        raise ValueError("dataset id must be a 32-character lowercase hex id")
    folder = output / "aem_staged/datasets" / dataset_id
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    events = pd.read_csv(folder / "events.csv", float_precision="round_trip")
    if manifest.get("id") != dataset_id:
        raise ValueError("staged latest pointer and manifest differ")
    if len(events) != manifest["events"] or _hash_events(events) != manifest["dataset_sha256"]:
        raise ValueError("dataset_event_population_hash_mismatch")
    contract = AemContract(**manifest["contract"])
    if contract.sha256 != manifest["contract_sha256"]:
        raise ValueError("dataset_contract_mismatch")
    for name in EXECUTION_DEPENDENCIES:
        expected = manifest["dependency_sha256"].get(name)
        path = (root / name).resolve()
        if expected is None or not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError(f"dataset_execution_dependency_changed:{name}")
    return dataset_id, folder, manifest, events, contract


def _observed_contexts(events, daily, minute, contract):
    actual = events.loc[events.decision == "TRADE"].copy()
    contexts = []
    for code, subset in actual.groupby("scrip_code", sort=True):
        days = daily_features(daily[code].sort_index())
        enriched = _enrich(minute[code], contract)
        sessions = {str(day): part for day, part in enriched.groupby(enriched.index.date)}
        for _, saved in subset.sort_values(["session_date", "event_id"]).iterrows():
            candidate = detect_daily_candidate(code, saved.symbol, days, saved.armed_on, contract)
            session = sessions.get(str(saved.session_date))
            if candidate is None or session is None or candidate.identifier != saved.candidate_id:
                raise ValueError(f"observed_context_mismatch:{saved.event_id}")
            decision = evaluate_trigger(
                candidate, session, pd.Timestamp(saved.available_at), contract
            )
            if decision.decision != "TRADE":
                raise ValueError(f"observed_trigger_mismatch:{saved.event_id}")
            contexts.append((saved, candidate, decision, session))
    return contexts


def _build_code_grid(code, subset, daily_frame, minute_frame, *, risk, costs, contract):
    """Replay one symbol; independent symbols may be scheduled concurrently."""
    print(f"AEM null: {code}, {len(subset)} observed attempts", flush=True)
    days = daily_features(daily_frame.sort_index())
    enriched = _enrich(minute_frame, contract)
    sessions = {str(day): part for day, part in enriched.groupby(enriched.index.date)}
    actual_rows = []
    grids = []
    for _, saved in subset.sort_values(["session_date", "event_id"]).iterrows():
        candidate = detect_daily_candidate(code, saved.symbol, days, saved.armed_on, contract)
        session = sessions.get(str(saved.session_date))
        if candidate is None or session is None or candidate.identifier != saved.candidate_id:
            raise ValueError(f"observed_context_mismatch:{saved.event_id}")
        decision = evaluate_trigger(candidate, session, pd.Timestamp(saved.available_at), contract)
        if decision.decision != "TRADE":
            raise ValueError(f"observed_trigger_mismatch:{saved.event_id}")
        _assert_replay(saved, label_trade(candidate, decision, session, risk, costs, contract))
        grid = counterfactual_grid(candidate, session, risk=risk, costs=costs, contract=contract)
        grid.insert(0, "session_date", str(saved.session_date))
        grid.insert(0, "event_id", saved.event_id)
        grid.insert(2, "scrip_code", code)
        actual_rows.append(saved)
        grids.append(grid)
    return pd.DataFrame(actual_rows), pd.concat(grids, ignore_index=True)


def _build_grid_parallel(events, daily, minute, *, risk, costs, contract, jobs=None):
    actual = events.loc[events.decision == "TRADE"].copy()
    if actual.empty or actual.event_id.isna().any() or actual.event_id.duplicated().any():
        raise ValueError("missing_or_invalid_observed_trade_attempts")
    groups = list(actual.groupby("scrip_code", sort=True))
    if jobs is None:
        jobs = min(8, max(1, os.cpu_count() or 1), len(groups))
    parts = Parallel(n_jobs=jobs, backend="loky")(
        delayed(_build_code_grid)(
            code,
            subset,
            daily[code],
            minute[code],
            risk=risk,
            costs=costs,
            contract=contract,
        )
        for code, subset in groups
    )
    observed = pd.concat([part[0] for part in parts], ignore_index=True)
    grid = pd.concat([part[1] for part in parts], ignore_index=True)
    return observed.sort_values(["session_date", "event_id"]).reset_index(drop=True), grid


def _stress_stats(rows: list[dict]) -> dict:
    attempts = len(rows)
    resolved = [row for row in rows if row["status"] == "resolved"]
    unresolved = [
        row
        for row in rows
        if row["status"]
        not in {"resolved", "chased", "unsizeable", "no_pullback_fill", "missed_fill"}
    ]
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
        "mean_net_r_per_attempt": float(np.sum(values) / attempts) if attempts else None,
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
    }


def _registered_stress(contexts, *, risk, costs, contract) -> dict:
    definitions = {
        "base_costs": (Decimal("1"), Decimal("1"), 0),
        "costs_1.25x": (Decimal("1.25"), Decimal("1"), 0),
        "costs_1.5x": (Decimal("1.5"), Decimal("1"), 0),
        "slippage_2x": (Decimal("1"), Decimal("2"), 0),
        "one_bar_delay": (Decimal("1"), Decimal("1"), 1),
    }
    results = Parallel(n_jobs=len(definitions), backend="loky")(
        delayed(_run_stress_case)(
            name,
            values,
            contexts,
            risk=risk,
            costs=costs,
            contract=contract,
        )
        for name, values in definitions.items()
    )
    saved_rows = dict(results)
    cases = {name: _stress_stats(rows) for name, rows in results}
    # Explicit adverse sensitivity: the best 10% of base resolved fills fail to execute.
    resolved = sorted(
        (row for row in saved_rows["base_costs"] if row["status"] == "resolved"),
        key=lambda row: (-float(row["net_r"]), row["event_id"]),
    )
    miss_count = math.ceil(len(resolved) * 0.10)
    missed_ids = {row["event_id"] for row in resolved[:miss_count]}
    missed = [
        (
            {
                "event_id": row["event_id"],
                "status": "missed_fill",
                "strict_success": None,
                "net_r": None,
            }
            if row["event_id"] in missed_ids
            else dict(row)
        )
        for row in saved_rows["base_costs"]
    ]
    cases["missed_fills"] = _stress_stats(missed)
    expected = [name for name in definitions] + ["missed_fills"]
    means = [cases[name]["mean_net_r"] for name in expected]
    if any(value is None for value in means):
        raise ValueError("registered stress case has no resolved fills")
    return {
        "cases": cases,
        "minimum_mean_net_r": min(means),
        "definitions": {
            "costs_1.25x": (
                "Scale each modeled buy/sell charge total by 1.25; unchanged signal population."
            ),
            "costs_1.5x": (
                "Scale each modeled buy/sell charge total by 1.50; unchanged signal population."
            ),
            "slippage_2x": "Double entry and exit slippage and rerun sizing/barrier execution.",
            "one_bar_delay": (
                "Execute the same frozen decision one M1 bar later; signal geometry is not "
                "recomputed."
            ),
            "missed_fills": (
                "Adverse sensitivity: convert the best-net-R 10% of resolved fills to no-fills."
            ),
        },
    }


def _run_stress_case(name, values, contexts, *, risk, costs, contract):
    charge_mult, slip_mult, delay = values
    model = _CostStress(costs, charge_mult, slip_mult)
    rows = []
    for saved, candidate, decision, session in contexts:
        replay_decision = decision
        if delay:
            available = pd.Timestamp(decision.available_at) + pd.Timedelta(minutes=delay)
            replay_decision = replace(decision, available_at=str(available))
        outcome = label_trade(candidate, replay_decision, session, risk, model, contract)
        if name == "base_costs":
            _assert_replay(saved, outcome)
        rows.append({"event_id": saved.event_id, **outcome})
    return name, rows


def _cached_grid(output, *, dataset_id, manifest_hash, events_hash):
    runs = output / "aem_staged_validation/runs"
    if not runs.is_dir():
        return None, None
    reports = sorted(
        runs.glob("*/report.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for report_path in reports:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            report.get("dataset_id") != dataset_id
            or report.get("source_manifest_sha256") != manifest_hash
            or report.get("source_events_csv_sha256") != events_hash
        ):
            continue
        path = report_path.parent / "counterfactual_grid.csv"
        if path.is_file():
            return pd.read_csv(path, float_precision="round_trip"), report["id"]
    return None, None


def _sector_map(root: Path, events: pd.DataFrame) -> dict[str, str]:
    membership = yaml.safe_load((root / "config/sector_membership.yaml").read_text("utf-8")) or {}
    by_symbol = {
        str(symbol).upper(): str(sector)
        for sector, symbols in membership.items()
        for symbol in symbols
    }
    return {
        str(row.scrip_code): by_symbol.get(str(row.symbol).upper(), "UNKNOWN")
        for row in events[["scrip_code", "symbol"]].drop_duplicates().itertuples()
    }


def _portfolio_replay(events, *, risk, costs, sectors, calendar) -> dict:
    rows = events.loc[(events.decision == "TRADE") & (events.status == "resolved")].copy()
    rows = rows.sort_values(["entry_at", "event_id"]).reset_index(drop=True)
    equity = float(risk.trading_capital)
    initial = equity
    open_positions: list[dict] = []
    selected = []
    rejections: Counter = Counter()
    consecutive_losses = 0
    pause_until = -1
    last_stop: dict[str, int] = {}
    week_key = None
    week_start = equity
    equity_curve = [equity]

    def close_through(at: pd.Timestamp, session_index: int) -> None:
        nonlocal equity, consecutive_losses, pause_until
        for position in sorted(open_positions.copy(), key=lambda item: item["exit_at"]):
            if position["exit_at"] > at:
                continue
            equity += position["net_pnl"]
            if position["net_pnl"] < 0:
                consecutive_losses += 1
                if consecutive_losses >= risk.consecutive_loss_pause.losses:
                    pause_until = session_index + 1 + risk.consecutive_loss_pause.sessions
                    consecutive_losses = 0
            else:
                consecutive_losses = 0
            if position["outcome"] in {"stop", "gap_stop"}:
                last_stop[position["scrip_code"]] = session_index
            open_positions.remove(position)

    by_day = {day: part for day, part in rows.groupby("session_date", sort=True)}
    for session_index, day in enumerate(calendar):
        on = pd.Timestamp(day).date()
        close_through(pd.Timestamp(f"{day} 09:15", tz="Asia/Kolkata"), session_index)
        key = on.isocalendar()[:2]
        if key != week_key:
            week_key, week_start = key, equity
        entries_today = 0
        for row in by_day.get(str(day), pd.DataFrame()).itertuples():
            entry_at = pd.Timestamp(row.entry_at)
            close_through(entry_at, session_index)
            code = str(row.scrip_code)
            clock = entry_at.strftime("%H:%M")
            reason = None
            if risk.no_entry_window.start <= clock < risk.no_entry_window.end:
                reason = "no_entry_window"
            elif any(item["scrip_code"] == code for item in open_positions):
                reason = "already_holding"
            elif len(open_positions) >= risk.max_open_positions:
                reason = "max_open_positions"
            elif entries_today >= risk.max_new_entries_per_day:
                reason = "max_new_entries_per_day"
            elif week_start > 0 and equity <= week_start * (1 - float(risk.weekly_loss_limit_pct)):
                reason = "weekly_loss_limit"
            elif session_index < pause_until:
                reason = "consecutive_loss_pause"
            elif (
                code in last_stop
                and session_index - last_stop[code] < risk.reentry_cooldown_sessions
            ):
                reason = "reentry_cooldown"
            elif (
                sum(sectors[item["scrip_code"]] == sectors[code] for item in open_positions)
                >= risk.max_per_sector
            ):
                reason = f"sector_cap:{sectors[code]}"
            open_risk = sum(item["risk"] for item in open_positions)
            available_heat = (
                float(risk.max_portfolio_heat_pct) - open_risk / equity if equity > 0 else 0
            )
            if reason is None and available_heat <= 0:
                reason = "portfolio_heat"
            if reason:
                rejections[reason] += 1
                continue
            size = position_size(
                SizeInputs(
                    equity=equity,
                    entry=float(row.entry),
                    stop=float(row.stop),
                    max_risk_pct=float(risk.max_risk_per_trade_pct),
                    max_position_value_pct=float(risk.max_position_value_pct),
                    gap_risk_cap_pct=float(risk.gap_risk_cap_pct),
                    available_heat_pct=available_heat,
                )
            )
            committed = sum(item["entry"] * item["qty"] for item in open_positions)
            qty = min(size.qty, float(max(0, int((equity - committed) / float(row.entry)))))
            if qty <= 0:
                rejections["zero_size_or_cash"] += 1
                continue
            charges = float(
                costs.leg_cost(
                    side=Side.BUY,
                    trade_type=TradeType.INTRADAY,
                    qty=qty,
                    price=price_decimal(row.entry),
                ).total
            )
            charges += float(
                costs.leg_cost(
                    side=Side.SELL,
                    trade_type=TradeType.INTRADAY,
                    qty=qty,
                    price=price_decimal(row.exit),
                    dp_applies=False,
                ).total
            )
            net = (float(row.exit) - float(row.entry)) * qty - charges
            risk_amount = (float(row.entry) - float(row.stop)) * qty
            position = {
                "event_id": row.event_id,
                "scrip_code": code,
                "entry": float(row.entry),
                "qty": qty,
                "risk": risk_amount,
                "net_pnl": net,
                "net_r": net / risk_amount,
                "strict_success": bool(row.target_hit and net > 0),
                "outcome": row.outcome,
                "exit_at": pd.Timestamp(row.exit_at),
            }
            open_positions.append(position)
            selected.append(position)
            entries_today += 1
        close_through(pd.Timestamp(f"{day} 23:59", tz="Asia/Kolkata"), session_index)
        equity_curve.append(equity)
    if open_positions:
        raise ValueError("portfolio replay ended with open positions")
    wins = sum(row["strict_success"] for row in selected)
    values = [row["net_r"] for row in selected]
    curve = np.asarray(equity_curve)
    return {
        "selected_fills": len(selected),
        "strict_successes": wins,
        "strict_success_rate": wins / len(selected) if selected else None,
        "mean_net_r_after_constraints": float(np.mean(values)) if values else None,
        "initial_capital": initial,
        "ending_capital": equity,
        "net_pnl": equity - initial,
        "max_drawdown": float(np.max(1 - curve / np.maximum.accumulate(curve))),
        "rejections": dict(sorted(rejections.items())),
        "enforced": [
            "no-entry window",
            "risk and position-value sizing",
            "cash",
            "portfolio heat",
            "max open positions",
            "max three entries per day",
            "weekly realised loss limit",
            "consecutive-loss pause",
            "stop re-entry cooldown",
            "sector cap",
        ],
        "contract_exception": (
            "Configured min_net_rr=2.0 is reported incompatible with the AEM quick-profit "
            "gross target/stop ratio and is not applied a second time to this frozen "
            "research contract."
        ),
    }


def run_staged_validation(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_id: str | None = None,
    n_cohorts: int = 500,
    seed: int = 20260924,
) -> dict:
    if not 1 <= n_cohorts <= 5000 or not 0 <= seed < 2**32:
        raise ValueError("cohorts must be 1..5000 and seed must be uint32")
    root, output = Path(root).resolve(), Path(output).resolve()
    run_id = uuid4().hex
    target = output / "aem_staged_validation/runs" / run_id
    target.mkdir(parents=True, exist_ok=False)
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "artifact_path": str(target / "report.json"),
        "dataset_id": dataset_id,
        "status": "running",
        "eligible_for_live": False,
        "evidence_class": "frozen_preperiod_selected_development_sample",
        "seed": seed,
        "cohorts_requested": n_cohorts,
    }
    try:
        dataset_id, folder, manifest, events, contract = _load_dataset(root, output, dataset_id)
        report["dataset_id"] = dataset_id
        report["source_manifest_sha256"] = digest(folder / "manifest.json")
        report["source_events_csv_sha256"] = digest(folder / "events.csv")
        settings = load_config(root)
        costs = nse_market(settings).costs
        daily, minute, _, calendar, evaluation, source = read_staged_aem_source(
            root,
            output,
            plan_id=manifest["plan_id"],
            contract=contract,
            benchmark_symbol=settings.universe.benchmark,
        )
        if source["sha256"] != manifest["source"]["sha256"]:
            raise ValueError("dataset_source_changed_rebuild_before_comparison")
        actual = events.loc[events.decision == "TRADE"].copy()
        grid, cached_from = _cached_grid(
            output,
            dataset_id=dataset_id,
            manifest_hash=report["source_manifest_sha256"],
            events_hash=report["source_events_csv_sha256"],
        )
        if grid is None:
            actual, grid = _build_grid_parallel(
                events, daily, minute, risk=settings.risk, costs=costs, contract=contract
            )
        else:
            report["counterfactual_grid_cached_from"] = cached_from
        grid.to_csv(target / "counterfactual_grid.csv", index=False)
        cohorts = sample_cohorts(
            grid,
            n_cohorts=n_cohorts,
            seed=seed,
            expected_slot_count=len(decision_times(actual.session_date.iloc[0], contract)),
        )
        cohorts.to_csv(target / "cohorts.csv", index=False)
        benchmark = {
            "id": run_id + ":matched_random",
            "dataset_id": dataset_id,
            "comparison": summarize_timing_null(actual, cohorts),
        }
        contexts = _observed_contexts(events, daily, minute, contract)
        stress = {
            "id": run_id + ":stress",
            "dataset_id": dataset_id,
            **_registered_stress(contexts, risk=settings.risk, costs=costs, contract=contract),
        }
        portfolio = {
            "id": run_id + ":portfolio",
            "dataset_id": dataset_id,
            **_portfolio_replay(
                events,
                risk=settings.risk,
                costs=costs,
                sectors=_sector_map(root, events),
                calendar=[str(day) for day in evaluation],
            ),
        }
        report.update(
            status="historical_diagnostic_only",
            observed_replay_verified=len(actual),
            grid_rows=len(grid),
            matched_random=benchmark,
            stress=stress,
            portfolio=portfolio,
        )
        report["scorecard"] = build_accuracy_scorecard(
            manifest, benchmark_report=benchmark, stress_report=stress, portfolio_report=portfolio
        )
        report["artifacts_sha256"] = {
            name: digest(target / name) for name in ("counterfactual_grid.csv", "cohorts.csv")
        }
        write_json(
            output / "aem_staged_validation/latest.json",
            {"id": run_id, "dataset_id": dataset_id, "path": str(target / "report.json")},
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        report.update(status="blocked_invalid_or_unavailable_source", error=str(error))
    report["limitations"] = [
        "Previously inspected historical data are diagnostic, not prospective evidence.",
        "Matched-random conditions on observed TRADE stock-days and does not test stock selection.",
        "Unknown sectors share one conservative capped bucket.",
        "No result authorizes live calls or supports a 70-80% reliability claim.",
    ]
    write_json(target / "report.json", report)
    return report
