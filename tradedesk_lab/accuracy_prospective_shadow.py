"""Milestone 8: forward-only validation of the frozen M7 accuracy candidate.

This collector has no authority over alerts, grades, sizing, management, or orders.  It
scores only NSE watchlists created after activation, freezes the top-two selection at the
arming close, and later grades the hypothetical next-session-open trade with the exact M7
geometry and current NSE costs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import joblib
import numpy as np
import pandas as pd

from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.signals import Signal
from tradedesk.markets.market import nse_market
from tradedesk.models import Side, TradeType, price_decimal, qty_decimal
from tradedesk.prediction.features import signal_features
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.accuracy_geometry import chronological_partition, wilson_lower_bound
from tradedesk_lab.accuracy_setup_stability import (
    DEFAULT_SETUP_STABILITY_PROTOCOL,
    SetupHypothesis,
    _hypothesis_frame,
    _model,
)
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.clean_dataset import FEATURES, load_prepared

IST = ZoneInfo("Asia/Kolkata")
VERSION = "accuracy-prospective-shadow-v1"
STATE_VERSION = 1
DEFAULT_OUTPUT = OUTPUT / "accuracy_prospective_shadow"
M7_ARTIFACT = ROOT / "data/models/accuracy-setup-stability/latest.json"
MODEL_FILE = "frozen_model.joblib"

MIN_RESOLVED = 100
MIN_ACTIVE_SESSIONS = 30
MIN_ACCURACY = 0.80
MIN_WILSON = 0.70
MIN_SESSION_TARGET_RATE = 0.70
MIN_EXPECTANCY_R = 0.0


def _now() -> datetime:
    return datetime.now(UTC)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@contextmanager
def _lock(output: Path) -> Iterator[None]:
    path = output / "collector.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("another M8 accuracy collector owns the lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _contract_hashes() -> dict[str, str]:
    names = (
        "tradedesk_lab/accuracy_prospective_shadow.py",
        "tradedesk_lab/accuracy_setup_stability.py",
        "tradedesk_lab/accuracy_geometry.py",
        "tradedesk_lab/clean_dataset.py",
        "src/tradedesk/engine/indicators.py",
        "src/tradedesk/prediction/features.py",
        "src/tradedesk/risk/sizing.py",
        "src/tradedesk/markets/costs.py",
        "src/tradedesk/risk/costs.py",
    )
    return {name: digest(ROOT / name) for name in names}


def _load_bars(con: duckdb.DuckDBPyConnection, code: str) -> pd.DataFrame:
    frame = con.execute(
        "SELECT ts,open,high,low,close,volume FROM candles "
        "WHERE scrip_code=? AND interval='1day' ORDER BY ts",
        [code],
    ).df()
    if frame.empty:
        return frame
    frame.index = (
        pd.to_datetime(frame.pop("ts"), unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
        .dt.normalize()
    )
    frame["volume"] = frame.volume.astype("int64")
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _index_codes(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return {
        str(symbol).upper(): str(code)
        for code, symbol in con.execute(
            "SELECT scrip_code,trading_symbol FROM instruments "
            "WHERE exch='NSE' AND kind='index'"
        ).fetchall()
    }


def _returns(bars: pd.DataFrame, armed: date) -> tuple[float | None, float | None]:
    close = bars.loc[bars.index.date <= armed, "close"].dropna()
    if len(close) < 2:
        return None, None
    one = (float(close.iloc[-1]) / float(close.iloc[-2]) - 1) * 100
    five = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100 if len(close) > 5 else None
    return one, five


def _feature_values(
    entry: dict[str, Any],
    watchlist: dict[str, Any],
    bars: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> dict[str, float]:
    signal = Signal.model_validate(entry["signal"])
    known = bars.loc[bars.index.date <= signal.armed_on]
    if known.empty or known.index[-1].date() != signal.armed_on:
        raise ValueError("missing or stale arming candle")
    nifty_1d, nifty_5d = _returns(benchmark, signal.armed_on)
    context = watchlist.get("regime") or {}
    values = signal_features(
        signal,
        daily_features(known),
        breadth_pct=context.get("breadth_pct"),
        vix=context.get("vix"),
        vix_change_5d=context.get("vix_change_5d_pct"),
        nifty_return_1d=nifty_1d,
        nifty_return_5d=nifty_5d,
    )
    result = {name: float(values[name]) for name in FEATURES}
    if not np.isfinite(list(result.values())).all():
        raise ValueError("non-finite M8 feature value")
    return result


def _watchlists(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((root / "data/watchlists").glob("*.json")):
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
            date.fromisoformat(value["on"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        value["_source_sha256"] = hashlib.sha256(raw).hexdigest()
        rows.append((path, value))
    return rows


def _score_deadline(armed: date) -> datetime:
    # Conservative: an unknown weekend/special session is treated as open, never skipped.
    return datetime.combine(armed + timedelta(days=1), time(9, 15), tzinfo=IST)


def _latest_closed_session(root: Path) -> date:
    settings = load_config(root)
    db = root / "data/tradedesk.duckdb"
    with duckdb.connect(str(db), read_only=True) as con:
        code = _index_codes(con).get(settings.universe.benchmark.upper())
        if code is None:
            raise ValueError("NSE benchmark code is unavailable")
        bars = _load_bars(con, code)
    if bars.empty:
        raise ValueError("NSE benchmark candles are unavailable")
    return bars.index[-1].date()


def _validate_m7(payload: dict[str, Any]) -> tuple[SetupHypothesis, float, int]:
    if payload.get("status") != "locked_pass":
        raise ValueError("M7 did not produce a locked historical pass")
    nominee = payload.get("nominee") or {}
    hypothesis = SetupHypothesis(**(nominee.get("hypothesis") or {}))
    point = nominee.get("development_operating_point") or {}
    threshold, top_k = float(point.get("threshold", -1)), int(point.get("top_k", -1))
    expected = ("trend_pullback", "next_session_open", 1.0, 0.5, 3, 0.55, 2)
    actual = (
        hypothesis.setup,
        hypothesis.entry_mode,
        hypothesis.stop_atr,
        hypothesis.target_r,
        hypothesis.max_hold,
        threshold,
        top_k,
    )
    if actual != expected:
        raise ValueError(f"M7 nominee differs from the preregistered M8 candidate: {actual}")
    return hypothesis, threshold, top_k


def activate(root: Path = ROOT, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Train once on M7 development data and start a no-backfill prospective cohort."""
    with _lock(output):
        state_path = output / "state.json"
        if state_path.exists():
            return _read(state_path)
        m7 = _read(M7_ARTIFACT)
        hypothesis, threshold, top_k = _validate_m7(m7)
        dataset = load_prepared(root)
        development, _, split_at = chronological_partition(
            dataset.frame, DEFAULT_SETUP_STABILITY_PROTOCOL.final_test_frac
        )
        slippage = float(nse_market(load_config(root)).costs.slippage_pct)
        frame = _hypothesis_frame(dataset, development, hypothesis, slippage_pct=slippage)
        model = _model()
        model.fit(frame[FEATURES].to_numpy(dtype=float), frame.label.to_numpy(dtype=int))

        output.mkdir(parents=True, exist_ok=True)
        model_path = output / MODEL_FILE
        temporary = model_path.with_suffix(".joblib.tmp")
        joblib.dump(model, temporary)
        temporary.replace(model_path)
        latest_watchlist = max(
            (date.fromisoformat(value["on"]) for _, value in _watchlists(root)),
            default=date.min,
        )
        # Also exclude the activation calendar date.  A stale local store must never make
        # a session that already happened in the real world look prospective when its data
        # arrives late (for example, activating on Saturday while Friday is not loaded yet).
        forward_after = max(
            _latest_closed_session(root),
            latest_watchlist,
            _now().astimezone(IST).date(),
        )
        activation = {
            "version": VERSION,
            "activated_at": _now().isoformat(),
            "forward_after": forward_after.isoformat(),
            "model_data_through": pd.Timestamp(frame.armed_on.max()).date().isoformat(),
            "historical_split_before": split_at.isoformat(),
            "training_rows": len(frame),
            "features": FEATURES,
            "feature_count": len(FEATURES),
            "model": "standardized_l2_logistic_c0.1",
            "model_sha256": digest(model_path),
            "m7_artifact_sha256": digest(M7_ARTIFACT),
            "m7_protocol_sha256": m7["protocol"]["sha256"],
            "contract_sha256": _contract_hashes(),
            "candidate": {
                "setup": hypothesis.setup,
                "entry_mode": hypothesis.entry_mode,
                "stop_atr": hypothesis.stop_atr,
                "target_r": hypothesis.target_r,
                "max_hold": hypothesis.max_hold,
                "probability_threshold": threshold,
                "top_k_per_arming_session": top_k,
            },
            "gate": {
                "minimum_resolved": MIN_RESOLVED,
                "minimum_active_sessions": MIN_ACTIVE_SESSIONS,
                "minimum_accuracy": MIN_ACCURACY,
                "minimum_wilson_lower": MIN_WILSON,
                "minimum_session_target_rate": MIN_SESSION_TARGET_RATE,
                "minimum_expectancy_r": MIN_EXPECTANCY_R,
            },
            "authority": "shadow_only_no_alert_grade_size_management_or_order_authority",
            "rule": "Only watchlists armed after forward_after count; no historical backfill.",
        }
        state = {
            "state_version": STATE_VERSION,
            "activation": activation,
            "records": [],
            "current_errors": [],
            "recent_errors": [],
        }
        state["summary"] = summarize(state)
        write_json(state_path, state)
        return state


def _load_frozen(state: dict[str, Any], output: Path) -> Any:
    activation = state["activation"]
    model_path = output / MODEL_FILE
    if digest(model_path) != activation["model_sha256"]:
        raise ValueError("frozen M8 model hash changed")
    if digest(M7_ARTIFACT) != activation["m7_artifact_sha256"]:
        raise ValueError("source M7 evidence artifact changed")
    if _contract_hashes() != activation["contract_sha256"]:
        raise ValueError("M8 contract code changed; activate a new named cohort")
    return joblib.load(model_path)


def _prediction_hash(record: dict[str, Any]) -> str:
    names = (
        "signal_id",
        "scrip_code",
        "symbol",
        "setup",
        "armed_on",
        "atr",
        "scored_at",
        "source_watchlist",
        "source_sha256",
        "features_sha256",
        "probability",
        "rank",
        "selected",
        "prospective_eligible",
        "selection_reason",
        "candidate",
    )
    return _hash({name: record.get(name) for name in names})


def _score_session(
    source: Path,
    watchlist: dict[str, Any],
    model: Any,
    bars_by_code: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    activation: dict[str, Any],
    scored_at: datetime,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for entry in watchlist.get("entries", []):
        signal = entry.get("signal") or {}
        if signal.get("setup") != activation["candidate"]["setup"]:
            continue
        values = _feature_values(
            entry, watchlist, bars_by_code[signal["scrip_code"]], benchmark
        )
        probability = float(
            model.predict_proba(pd.DataFrame([values], columns=FEATURES))[0, 1]
        )
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("M8 model returned an invalid probability")
        candidates.append(
            {
                "entry": entry,
                "features": values,
                "probability": probability,
            }
        )
    candidates.sort(
        key=lambda row: (-row["probability"], row["entry"]["signal"]["id"])
    )
    armed = date.fromisoformat(watchlist["on"])
    deadline = _score_deadline(armed)
    generated = datetime.fromisoformat(str(watchlist["generated_at"]))
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=IST)
    after_activation = generated.astimezone(UTC) >= datetime.fromisoformat(
        activation["activated_at"]
    )
    prospective = (
        armed > date.fromisoformat(activation["forward_after"])
        and scored_at.astimezone(IST) >= datetime.combine(armed, time(15, 30), tzinfo=IST)
        and scored_at.astimezone(IST) < deadline
        and scored_at >= generated.astimezone(UTC)
        and after_activation
    )
    threshold = float(activation["candidate"]["probability_threshold"])
    top_k = int(activation["candidate"]["top_k_per_arming_session"])
    records = []
    for rank, row in enumerate(candidates, start=1):
        selected = row["probability"] >= threshold and rank <= top_k
        if not prospective:
            reason = "excluded_late_or_pre_activation_score"
        elif selected:
            reason = "frozen_threshold_and_top_k"
        elif row["probability"] < threshold:
            reason = "below_probability_threshold"
        else:
            reason = "outside_top_k"
        signal = row["entry"]["signal"]
        record = {
            "signal_id": signal["id"],
            "scrip_code": signal["scrip_code"],
            "symbol": signal["symbol"],
            "setup": signal["setup"],
            "armed_on": signal["armed_on"],
            "atr": float(signal["atr"]),
            "source_watchlist": source.name,
            "source_sha256": watchlist["_source_sha256"],
            "scored_at": scored_at.isoformat(),
            "score_deadline": deadline.isoformat(),
            "features": row["features"],
            "features_sha256": _hash(row["features"]),
            "probability": row["probability"],
            "rank": rank,
            "selected": selected,
            "prospective_eligible": prospective,
            "selection_reason": reason,
            "candidate": activation["candidate"].copy(),
            "status": "pending" if selected and prospective else "not_selected",
            "entry_date": None,
            "fill_price": None,
            "stop": None,
            "target": None,
            "qty": None,
            "label": None,
            "outcome": None,
            "net_r": None,
            "resolved_at": None,
        }
        record["prediction_sha256"] = _prediction_hash(record)
        records.append(record)
    return records


def _quick_outcome(
    future: pd.DataFrame,
    *,
    fill: float,
    stop: float,
    target: float,
    max_hold: int,
    qty: float,
    costs: Any,
) -> tuple[int, float, float, date, str] | None:
    """The exact full-exit accounting used by M7's frozen geometry."""
    if len(future) < max_hold + 1:
        return None
    exit_price: float | None = None
    exit_on: date | None = None
    target_hit = False
    outcome = ""
    slip = 1 - float(costs.slippage_pct)
    for session, (stamp, bar) in enumerate(future.iloc[: max_hold + 1].iterrows()):
        if session > 0 and float(bar.open) <= stop:
            exit_price, outcome = float(bar.open) * slip, "gap_stop"
        elif float(bar.low) <= stop:
            exit_price, outcome = stop * slip, "stop"
        elif float(bar.high) >= target:
            exit_price, target_hit, outcome = target * slip, True, "target"
        elif session == max_hold:
            exit_price, outcome = float(bar.close) * slip, "max_hold"
        if exit_price is not None:
            exit_on = pd.Timestamp(stamp).date()
            break
    if exit_price is None or exit_on is None:
        return None
    entry_on = pd.Timestamp(future.index[0]).date()
    trade_type = TradeType.INTRADAY if exit_on == entry_on else TradeType.DELIVERY
    charges = costs.leg_cost(
        side=Side.BUY,
        trade_type=trade_type,
        qty=qty,
        price=price_decimal(fill),
    ).total
    charges += costs.leg_cost(
        side=Side.SELL,
        trade_type=trade_type,
        qty=qty,
        price=price_decimal(exit_price),
        dp_applies=trade_type == TradeType.DELIVERY,
    ).total
    gross = (price_decimal(exit_price) - price_decimal(fill)) * qty_decimal(qty)
    net = gross - Decimal(charges)
    risk = (price_decimal(fill) - price_decimal(stop)) * qty_decimal(qty)
    return int(target_hit and net > 0), float(net / risk), exit_price, exit_on, outcome


def _resolve_record(
    record: dict[str, Any],
    bars: pd.DataFrame,
    sessions: list[date],
    activation: dict[str, Any],
    root: Path,
) -> bool:
    if record["status"] != "pending":
        return False
    if record["prediction_sha256"] != _prediction_hash(record):
        raise ValueError("frozen M8 prediction was modified")
    armed = date.fromisoformat(record["armed_on"])
    later = [day for day in sessions if day > armed]
    if not later:
        return False
    entry_on = later[0]
    future = bars.loc[bars.index.date >= entry_on]
    if future.empty or future.index[0].date() != entry_on:
        raise ValueError(f"missing next-session candle for {record['scrip_code']}")
    candidate = activation["candidate"]
    max_hold = int(candidate["max_hold"])
    if len(future) < max_hold + 1:
        return False
    settings = load_config(root)
    market = nse_market(settings)
    fill = float(future.iloc[0].open) * (1 + float(market.costs.slippage_pct))
    stop = fill - float(candidate["stop_atr"]) * float(record["atr"])
    target = fill + float(candidate["target_r"]) * (fill - stop)
    size = position_size(
        SizeInputs(
            equity=float(settings.risk.trading_capital),
            entry=fill,
            stop=stop,
            max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
            max_position_value_pct=float(settings.risk.max_position_value_pct),
            size_multiplier=float(settings.risk.regime_size_multiplier.neutral),
            gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
            gap95_pct=gap95_pct(bars.loc[bars.index.date <= armed]),
            available_heat_pct=float(settings.risk.max_portfolio_heat_pct),
        )
    )
    if size.qty <= 0:
        record.update(status="excluded", outcome="unsizeable", resolved_at=_now().isoformat())
        return True
    result = _quick_outcome(
        future,
        fill=fill,
        stop=stop,
        target=target,
        max_hold=max_hold,
        qty=size.qty,
        costs=market.costs,
    )
    if result is None:
        return False
    label, net_r, exit_price, exit_on, outcome = result
    record.update(
        status="resolved",
        entry_date=entry_on.isoformat(),
        fill_price=fill,
        stop=stop,
        target=target,
        qty=size.qty,
        sizing_caps=size.caps,
        label=label,
        outcome=outcome,
        net_r=net_r,
        exit_price=exit_price,
        exit_date=exit_on.isoformat(),
        resolved_at=_now().isoformat(),
    )
    return True


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    eligible = [
        row
        for row in state.get("records", [])
        if row.get("prospective_eligible") is True and row.get("selected") is True
    ]
    resolved = [row for row in eligible if row.get("status") == "resolved"]
    wins = sum(int(row["label"]) for row in resolved)
    accuracy = wins / len(resolved) if resolved else None
    wilson = wilson_lower_bound(wins, len(resolved)) if resolved else None
    session_rates: dict[str, list[int]] = {}
    for row in resolved:
        session_rates.setdefault(row["armed_on"], []).append(int(row["label"]))
    successful_sessions = sum(np.mean(labels) >= 0.80 for labels in session_rates.values())
    session_rate = successful_sessions / len(session_rates) if session_rates else None
    expectancy = (
        float(np.mean([float(row["net_r"]) for row in resolved])) if resolved else None
    )
    checks = {
        "resolved_calls": len(resolved) >= MIN_RESOLVED,
        "active_sessions": len(session_rates) >= MIN_ACTIVE_SESSIONS,
        "accuracy": accuracy is not None and accuracy >= MIN_ACCURACY,
        "wilson_lower_bound": wilson is not None and wilson >= MIN_WILSON,
        "session_target_rate": session_rate is not None
        and session_rate >= MIN_SESSION_TARGET_RATE,
        "expectancy_r": expectancy is not None and expectancy > MIN_EXPECTANCY_R,
    }
    enough = checks["resolved_calls"] and checks["active_sessions"]
    status = (
        "prospective_pass"
        if enough and all(checks.values())
        else "prospective_fail" if enough else "collecting_insufficient_evidence"
    )
    return {
        "status": status,
        "evaluated_candidates": sum(
            row.get("prospective_eligible") is True for row in state.get("records", [])
        ),
        "selected_calls": len(eligible),
        "pending_calls": sum(row.get("status") == "pending" for row in eligible),
        "excluded_calls": sum(row.get("status") == "excluded" for row in eligible),
        "resolved_calls": len(resolved),
        "wins": wins,
        "accuracy": accuracy,
        "wilson_lower_bound": wilson,
        "active_sessions": len(session_rates),
        "successful_sessions": successful_sessions,
        "session_target_rate": session_rate,
        "expectancy_r": expectancy,
        "gate_checks": checks,
        "eligible_for_live": False,
        "authority": "shadow_only",
        "updated_at": _now().isoformat(),
    }


def collect(root: Path = ROOT, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Score unseen watchlists and resolve selected calls; idempotent and fail closed."""
    with _lock(output):
        state_path = output / "state.json"
        if not state_path.exists():
            raise FileNotFoundError("M8 is not activated; run the activation command once")
        state = _read(state_path)
        model = _load_frozen(state, output)
        activation = state["activation"]
        scored_at = _now()
        errors: list[dict[str, Any]] = []
        cache: dict[str, pd.DataFrame] = {}
        settings = load_config(root)
        with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
            benchmark_code = _index_codes(con).get(settings.universe.benchmark.upper())
            if benchmark_code is None:
                raise ValueError("NSE benchmark code is unavailable")

            def bars(code: str) -> pd.DataFrame:
                if code not in cache:
                    cache[code] = _load_bars(con, code)
                return cache[code]

            benchmark = bars(benchmark_code)
            for source, watchlist in _watchlists(root):
                if date.fromisoformat(watchlist["on"]) <= date.fromisoformat(
                    activation["forward_after"]
                ):
                    continue
                # A session is frozen atomically.  If the source file is later overwritten
                # or appended, never rank just the new tail against an already-frozen top-k.
                if any(row["armed_on"] == watchlist["on"] for row in state["records"]):
                    continue
                entries = [
                    entry
                    for entry in watchlist.get("entries", [])
                    if (entry.get("signal") or {}).get("setup")
                    == activation["candidate"]["setup"]
                ]
                if not entries:
                    continue
                scoped = {**watchlist, "entries": entries}
                try:
                    codes = {entry["signal"]["scrip_code"] for entry in entries}
                    records = _score_session(
                        source,
                        scoped,
                        model,
                        {code: bars(code) for code in codes},
                        benchmark,
                        activation,
                        scored_at,
                    )
                    state["records"].extend(records)
                except Exception as exc:
                    errors.append(
                        {
                            "watchlist": source.name,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            sessions = list(benchmark.index.date)
            for record in state["records"]:
                try:
                    _resolve_record(
                        record,
                        bars(record["scrip_code"]),
                        sessions,
                        activation,
                        root,
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "signal_id": record["signal_id"],
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        timestamped = [{**row, "at": _now().isoformat()} for row in errors]
        state["current_errors"] = timestamped
        state["recent_errors"] = (state.get("recent_errors", []) + timestamped)[-100:]
        state["records"].sort(key=lambda row: (row["armed_on"], row["rank"]))
        state["summary"] = summarize(state)
        write_json(state_path, state)
        return state
