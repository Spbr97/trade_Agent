"""Prospective shadow evidence: frozen scores, later outcomes, no production writes."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from datetime import time as daytime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import yaml

from tradedesk.backtest.fills import Bar, EntryOutcome, evaluate_entry
from tradedesk.config import load_config
from tradedesk.config.models import ChargeSchedule
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.signals import Signal
from tradedesk.markets.costs import EquityCostModel
from tradedesk.prediction.features import signal_features
from tradedesk.reliability import wilson_lower_bound
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.outcomes import CONTRACT_VERSION, geometry_error, simulate_outcome
from tradedesk_lab.portable import load_portable
from tradedesk_lab.registry import Registry

STATE_VERSION = 2
IST = ZoneInfo("Asia/Kolkata")
TERMINAL = {
    "resolved",
    "chased",
    "invalidated",
    "expired",
    "invalid_geometry",
    "unsizeable",
    "rejected_geometry",
    "untradeable",
}


@contextmanager
def _lock(output: Path, name: str) -> Iterator[None]:
    """An OS lock survives neither a crash nor a reboot; no stale PID deletion needed."""
    path = output / "forward" / f"{name}.lock"
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
            raise RuntimeError(f"Another forward {name} process already owns the lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def now() -> str:
    return datetime.now(UTC).isoformat()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _contract_hashes() -> dict[str, str]:
    """Freeze outcome, feature, model-loader and execution implementations per cohort."""
    names = (
        "tradedesk_lab/forward.py",
        "tradedesk_lab/outcomes.py",
        "tradedesk_lab/portable.py",
        "src/tradedesk/backtest/fills.py",
        "src/tradedesk/engine/indicators.py",
        "src/tradedesk/prediction/features.py",
        "src/tradedesk/risk/sizing.py",
        "src/tradedesk/markets/costs.py",
        "src/tradedesk/risk/costs.py",
    )
    return {name: digest(ROOT / name) for name in names if (ROOT / name).is_file()}


def _closed_bars(bars: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    if bars.empty:
        return bars
    local = as_of.astimezone(IST)
    latest = local.date() if local.time() >= daytime(15, 30) else local.date() - timedelta(days=1)
    return bars.loc[bars.index.date <= latest]


def _calendar(root: Path) -> tuple[set[date], str | None]:
    """Only trust an explicitly sourced local closure list; never infer a holiday."""
    for path in (root / "data/calendar/nse_holidays.json", root / "config/nse_holidays.json"):
        if not path.exists():
            continue
        data = _read(path)
        if not data.get("source"):
            raise ValueError(f"Holiday calendar {path.name} must identify its source")
        return {date.fromisoformat(day) for day in data["holidays"]}, digest(path)
    return set(), None


def _score_deadline(armed: date, holidays: set[date]) -> datetime:
    # Unknown days, including weekends with possible special sessions, are treated as
    # potentially open. This can exclude valid weekend scores, never admit late scores.
    following = armed + timedelta(days=1)
    while following in holidays:
        following += timedelta(days=1)
    return datetime.combine(following, daytime(9, 15), tzinfo=IST)


def _prediction_hash(record: dict[str, Any]) -> str:
    names = (
        "signal_id",
        "source_sha256",
        "armed_on",
        "scored_at",
        "signal",
        "symbol",
        "scrip_code",
        "setup",
        "qty",
        "rejected_for",
        "features",
        "features_sha256",
        "probabilities",
        "ensemble_probability",
        "spread",
        "agreement",
        "selected_for_research",
        "model_run_id",
        "artifact_sha256",
        "contract_sha256",
        "contract_version",
        "model_label_contract",
        "economics",
        "score_deadline",
        "prospective_eligible",
        "evidence_class",
        "selection_reason",
        "calendar_sha256",
    )
    return _feature_hash({name: record.get(name) for name in names})


def _entry_hash(record: dict[str, Any]) -> str:
    names = ("entry_date", "fill_price", "hypothetical_qty", "entry_at_open", "entry_recorded_at")
    return _feature_hash({name: record.get(name) for name in names})


def _migrate(state: dict[str, Any]) -> None:
    """Retain every original score/outcome; legacy timing cannot be proved after the fact."""
    if state.get("version", 1) >= STATE_VERSION:
        return
    for record in state["records"]:
        record.setdefault("legacy_payload_sha256", _feature_hash(record))
        record["prospective_eligible"] = False
        record["evidence_class"] = "legacy_unverified"
        record["verification_reason"] = "Recorded before timestamp and net-success contract"
    state["version"] = STATE_VERSION
    state["migrated_at"] = now()


def _watchlists(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    result = []
    for path in sorted((root / "data/watchlists").glob("*.json")):
        try:
            payload = path.read_bytes()
            value = json.loads(payload)
            date.fromisoformat(value["on"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        value["_collector_sha256"] = hashlib.sha256(payload).hexdigest()
        result.append((path, value))
    return result


def _load_frozen(
    output: Path, run_id: str | None = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    frozen = run_id or _read(output / "latest.json")["id"]
    folder = output / "runs" / frozen
    report = _read(folder / "report.json")
    with Registry(output / "registry.sqlite", readonly=True) as registry:
        run = registry.run(frozen)
    if report.get("id") != frozen:
        raise ValueError("Frozen report ID does not match its cohort")
    if not run or run["status"] != "completed":
        raise ValueError("The frozen research run is not complete")
    artifacts = {
        row["family"]: row
        for row in run["candidates"]
        if row.get("artifact_path") and row.get("artifact_sha256")
    }
    bundles, hashes = {}, {}
    for family, row in artifacts.items():
        path = output / row["artifact_path"]
        actual = digest(path)
        if actual != row["artifact_sha256"]:
            raise ValueError(f"Frozen {family} artifact hash does not match the registry")
        bundles[family] = load_portable(path)
        hashes[family] = actual
    if len(bundles) < 2:
        raise ValueError("Forward agreement requires at least two frozen model families")
    feature_sets = {tuple(bundle.features) for bundle in bundles.values()}
    if len(feature_sets) != 1:
        raise ValueError("Frozen model families do not share one feature contract")
    return report, bundles, hashes


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


def _returns(bars: pd.DataFrame, on: date) -> tuple[float | None, float | None]:
    if bars.empty:
        return None, None
    close = bars.loc[bars.index.date <= on, "close"].dropna()
    if len(close) < 2:
        return None, None
    one = (float(close.iloc[-1]) / float(close.iloc[-2]) - 1) * 100
    five = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100 if len(close) > 5 else None
    return one, five


def _instrument_maps(
    con: duckdb.DuckDBPyConnection, root: Path
) -> tuple[str | None, dict[str, str], dict[str, str]]:
    settings = load_config(root)
    rows = con.execute(
        "SELECT scrip_code,trading_symbol,kind FROM instruments WHERE exch='NSE'"
    ).fetchall()
    equity = {str(symbol).upper(): str(code) for code, symbol, kind in rows if kind == "equity"}
    indices = {str(symbol).upper(): str(code) for code, symbol, kind in rows if kind == "index"}
    membership = (
        yaml.safe_load((root / "config/sector_membership.yaml").read_text(encoding="utf-8")) or {}
    )
    sector_of = {
        equity[symbol.upper()]: sector
        for sector, symbols in membership.items()
        for symbol in symbols
        if symbol.upper() in equity and sector.upper() in indices
    }
    sector_codes = {
        sector: indices[sector.upper()] for sector in membership if sector.upper() in indices
    }
    return indices.get(settings.universe.benchmark.upper()), sector_of, sector_codes


def feature_values(
    entry: dict[str, Any],
    watchlist: dict[str, Any],
    bars: pd.DataFrame,
    features: list[str],
    *,
    nifty: tuple[float | None, float | None] = (None, None),
    sector: tuple[float | None, float | None] = (None, None),
) -> dict[str, float]:
    """Construct features strictly at the arming close; later bars are always sliced away."""
    signal = Signal.model_validate(entry["signal"])
    armed = signal.armed_on
    known = bars.loc[bars.index.date <= armed]
    if known.empty or known.index[-1].date() != armed:
        raise ValueError(f"No arming-date candle for {signal.scrip_code} on {armed}")
    context = watchlist.get("regime") or {}
    values = signal_features(
        signal,
        daily_features(known),
        breadth_pct=context.get("breadth_pct"),
        vix=context.get("vix"),
        vix_change_5d=context.get("vix_change_5d_pct"),
        nifty_return_1d=nifty[0],
        nifty_return_5d=nifty[1],
        sector_return_1d=sector[0],
        sector_return_5d=sector[1],
    )
    missing = set(features) - set(values)
    if missing:
        raise ValueError(f"Forward feature contract is missing: {sorted(missing)}")
    return {name: float(values[name]) for name in features}


def _serializable(values: dict[str, float]) -> dict[str, float | None]:
    return {name: value if np.isfinite(value) else None for name, value in values.items()}


def _feature_hash(values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _score(
    entry: dict[str, Any],
    watchlist: dict[str, Any],
    path: Path,
    bundles: dict[str, Any],
    bars: pd.DataFrame,
    values: dict[str, float],
    report: dict[str, Any],
    activation: dict[str, Any],
    scored_at: datetime | None,
    holidays: set[date],
    calendar_hash: str | None,
) -> dict[str, Any]:
    frame = pd.DataFrame([values], columns=next(iter(bundles.values())).features)
    probabilities = {family: float(bundle.predict(frame)[0]) for family, bundle in bundles.items()}
    # Capture completion time, not the beginning of a potentially long collection cycle.
    scored_at = scored_at or datetime.fromisoformat(now())
    values_array = np.array(list(probabilities.values()), dtype=float)
    if not np.all(np.isfinite(values_array) & (values_array >= 0) & (values_array <= 1)):
        raise ValueError("Frozen models returned an invalid probability")
    mean = float(values_array.mean())
    spread = float(np.ptp(values_array))
    agrees = spread <= 0.10
    champion = report.get("champion")
    selected = False
    reason = "Frozen development run found no positive-net champion; observation only"
    if champion:
        reason = "Champion exists, but this isolated collector has no live-selection authority"
    clean = _serializable(values)
    signal = entry["signal"]
    armed = date.fromisoformat(signal["armed_on"])
    deadline = _score_deadline(armed, holidays)
    after_close = scored_at >= datetime.combine(armed, daytime(15, 30), tzinfo=IST)
    prospective = after_close and scored_at < deadline
    record = {
        "signal_id": signal["id"],
        "source_watchlist": path.name,
        "source_sha256": watchlist.get("_collector_sha256") or digest(path),
        "armed_on": signal["armed_on"],
        "scored_at": scored_at.isoformat(),
        "score_deadline": deadline.isoformat(),
        "prospective_eligible": prospective,
        "evidence_class": "prospective" if prospective else "retrospective_late",
        "calendar_sha256": calendar_hash,
        "model_run_id": report["id"],
        "model_label_contract": report.get("metadata", {}).get(
            "label_version", report.get("metadata", {}).get("contract_version", "legacy-v1")
        ),
        "artifact_sha256": deepcopy(activation["artifact_sha256"]),
        "contract_sha256": deepcopy(activation["contract_sha256"]),
        "contract_version": CONTRACT_VERSION,
        "economics": deepcopy(activation["economics"]),
        "symbol": signal["symbol"],
        "scrip_code": signal["scrip_code"],
        "setup": signal["setup"],
        "signal": deepcopy(signal),
        "qty": entry.get("qty"),
        "rejected_for": entry.get("rejected_for", []),
        "features": clean,
        "features_sha256": _feature_hash(clean),
        "probabilities": probabilities,
        "ensemble_probability": mean,
        "spread": spread,
        "agreement": agrees,
        "selected_for_research": selected,
        "selection_reason": reason,
        "status": "pending_entry",
        "entry_date": None,
        "fill_price": None,
        "outcome": None,
        "label": None,
        "sessions_to_outcome": None,
        "exit_price": None,
        "gross_r": None,
        "net_r": None,
        "net_pnl": None,
        "costs": None,
        "target_hit": None,
        "strict_success": None,
        "resolved_at": None,
        "last_evaluated_candle": str(bars.index[-1].date()) if len(bars) else None,
    }
    record["prediction_sha256"] = _prediction_hash(record)
    return record


def resolve_record(
    record: dict[str, Any],
    bars: pd.DataFrame,
    slippage_pct: float,
    *,
    costs: Any = None,
    session_dates: list[date] | None = None,
) -> bool:
    """Replay executable fills with full indicator history and frozen fee/sizing inputs."""
    if record.get("evidence_class") == "legacy_unverified":
        return False
    if record.get("prediction_sha256") and record["prediction_sha256"] != _prediction_hash(record):
        raise ValueError("Frozen prediction payload was modified")
    if record.get("entry_sha256") and record["entry_sha256"] != _entry_hash(record):
        raise ValueError("Frozen entry payload was modified")
    if record["status"] in TERMINAL:
        return False
    signal = Signal.model_validate(record["signal"])
    featured = daily_features(bars)
    future = featured.loc[featured.index.date > signal.armed_on]
    before = json.dumps(record, sort_keys=True, default=str)
    record["last_evaluated_candle"] = str(bars.index[-1].date()) if len(bars) else None
    economics = record.get("economics")
    if costs is None:
        costs = EquityCostModel(ChargeSchedule.model_validate((economics or {}).get("costs", {})))
    known_sessions = sorted(
        set(session_dates if session_dates is not None else list(future.index.date))
    )
    if session_dates is not None and not future.empty:
        if not known_sessions or future.index[-1].date() > known_sessions[-1]:
            raise ValueError("Benchmark session calendar is missing or stale")
    if session_dates is not None and not record.get("entry_date"):
        expected = [day for day in known_sessions if signal.armed_on < day][: signal.valid_sessions]
        missing = set(expected) - set(future.index.date)
        if missing:
            raise ValueError(f"Cannot infer entry over missing candles: {min(missing)}")
    if record.get("entry_date"):
        result = simulate_outcome(
            signal,
            featured,
            date.fromisoformat(record["entry_date"]),
            float(record["fill_price"]),
            float(record["hypothetical_qty"]),
            costs,
            entry_at_open=record.get("entry_at_open", False),
        )
        record.update(result)
        if record["status"] == "resolved":
            record["resolved_at"] = now()
        return before != json.dumps(record, sort_keys=True, default=str)
    for session, (stamp, row) in enumerate(future.iterrows(), start=1):
        if session_dates is not None:
            session = sum(signal.armed_on < day <= stamp.date() for day in known_sessions)
        if session > signal.valid_sessions and record.get("entry_date") is None:
            record.update(status="expired", outcome="expired", resolved_at=now())
            break
        bar = Bar(
            on=stamp.date(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=int(row.volume),
            ema10=float(row.ema10),
            atr=float(row.atr14),
        )
        outcome, fill = evaluate_entry(signal, bar, slippage_pct)
        if outcome is EntryOutcome.CHASED:
            record.update(status="chased", outcome="chased", resolved_at=now())
            break
        if outcome is EntryOutcome.INVALIDATED:
            record.update(status="invalidated", outcome="invalidated", resolved_at=now())
            break
        if outcome is EntryOutcome.FILLED:
            assert fill is not None
            error = geometry_error(float(fill), signal.stop, signal.t1)
            if error:
                record.update(
                    status="invalid_geometry",
                    outcome="invalid_geometry",
                    exclusion_reason=error,
                    resolved_at=now(),
                )
                break
            if economics:
                multiplier = economics["regime_size_multiplier"].get(signal.regime, 0.5)
                size = position_size(
                    SizeInputs(
                        equity=float(economics["trading_capital"]),
                        entry=float(fill),
                        stop=signal.stop,
                        max_risk_pct=float(economics["max_risk_per_trade_pct"]),
                        max_position_value_pct=float(economics["max_position_value_pct"]),
                        size_multiplier=float(multiplier),
                        gap_risk_cap_pct=float(economics["gap_risk_cap_pct"]),
                        gap95_pct=gap95_pct(bars.loc[bars.index.date <= signal.armed_on]),
                        available_heat_pct=float(economics["max_portfolio_heat_pct"]),
                    )
                )
                qty = size.qty
                record["sizing_caps"] = size.caps
            else:
                qty = float(record.get("qty") or 0)
            if qty <= 0:
                record.update(status="unsizeable", outcome="unsizeable", resolved_at=now())
                break
            record.update(entry_date=str(stamp.date()), fill_price=float(fill))
            record["hypothetical_qty"] = qty
            record["entry_at_open"] = bar.open >= signal.trigger
            record["entry_recorded_at"] = now()
            record["entry_sha256"] = _entry_hash(record)
            result = simulate_outcome(
                signal,
                featured,
                stamp.date(),
                float(fill),
                qty,
                costs,
                entry_at_open=record["entry_at_open"],
            )
            record.update(result)
            if record["status"] == "resolved":
                record["resolved_at"] = now()
            break
    if record["status"] == "pending_entry":
        elapsed = sum(signal.armed_on < day for day in known_sessions)
        if elapsed >= signal.valid_sessions:
            record.update(status="expired", outcome="expired", resolved_at=now())
    return before != json.dumps(record, sort_keys=True, default=str)


def summarize(state: dict[str, Any], latest_candle: str | None) -> dict[str, Any]:
    records = state["records"]
    eligible = [row for row in records if row.get("prospective_eligible") is True]
    resolved = [row for row in eligible if row["status"] == "resolved"]
    selected = [row for row in eligible if row.get("selected_for_research")]
    selected_done = [row for row in selected if row["status"] == "resolved"]

    def evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(row.get("strict_success") is True for row in rows)
        gross = [row["gross_r"] for row in rows if row.get("gross_r") is not None]
        net = [row["net_r"] for row in rows if row.get("net_r") is not None]
        return {
            "resolved": len(rows),
            "hits": wins,
            "hit_rate": wins / len(rows) if rows else None,
            "wilson_lower": float(wilson_lower_bound(wins, len(rows))) if rows else None,
            "mean_gross_r": float(np.mean(gross)) if gross else None,
            "mean_net_r": float(np.mean(net)) if net else None,
            "net_pnl": sum(float(row.get("net_pnl") or 0) for row in rows),
            "target_hits": sum(row.get("target_hit") is True for row in rows),
        }

    by_setup = {
        setup: evidence([row for row in resolved if row["setup"] == setup])
        for setup in sorted({row["setup"] for row in records})
    }
    return {
        "total_scored": len(records),
        "prospective_scored": len(eligible),
        "late_scored": sum(row.get("evidence_class") == "retrospective_late" for row in records),
        "legacy_unverified": sum(
            row.get("evidence_class") == "legacy_unverified" for row in records
        ),
        "pending_entry": sum(row["status"] == "pending_entry" for row in eligible),
        "triggered_pending": sum(row["status"] == "triggered_pending" for row in eligible),
        "untriggered_terminal": sum(row["status"] in TERMINAL - {"resolved"} for row in eligible),
        "contract_version": CONTRACT_VERSION,
        "all_scored": evidence(resolved),
        "selected_calls": len(selected),
        "selected": evidence(selected_done),
        "by_setup": by_setup,
        "by_session": {
            day: evidence([row for row in resolved if row["entry_date"] == day])
            for day in sorted({row["entry_date"] for row in resolved})
        },
        "retrospective": evidence(
            [
                row
                for row in records
                if row.get("evidence_class") == "retrospective_late" and row["status"] == "resolved"
            ]
        ),
        "latest_candle": latest_candle,
        "minimum_early_read": 30,
        "minimum_oos_evidence": 100,
        "production_minimum_per_setup": 500,
        "updated_at": now(),
    }


def collect(root: Path = ROOT, output: Path = OUTPUT) -> dict[str, Any]:
    """Score only post-activation watchlists, then resolve frozen records idempotently."""
    with _lock(output, "collection"):
        return _collect(root, output)


def _collect(root: Path, output: Path) -> dict[str, Any]:
    path = output / "forward/state.json"
    as_of = datetime.fromisoformat(now())
    state = _read(path) if path.exists() else None
    pinned = state["activation"]["model_run_id"] if state else None
    report, bundles, hashes = _load_frozen(output, pinned)
    settings = load_config(root)
    risk = settings.risk.model_dump(mode="json")
    contract_hashes = _contract_hashes()
    watchlists = _watchlists(root)
    if state is None:
        latest_existing = max([report["metadata"]["to"], *(value["on"] for _, value in watchlists)])
        state = {
            "version": STATE_VERSION,
            "activation": {
                "activated_at": now(),
                "model_run_id": report["id"],
                "model_data_through": report["metadata"]["to"],
                "forward_after": latest_existing,
                "artifact_sha256": hashes,
                "contract_version": CONTRACT_VERSION,
                "contract_sha256": contract_hashes,
                "economics": risk,
                "rule": (
                    "Post-activation watchlists scored after arming close and before the next "
                    "09:15 IST cutoff only; unknown calendar days are conservatively treated "
                    "as open. Legacy/late scores are excluded from prospective evidence."
                ),
            },
            "records": [],
            "recent_errors": [],
        }
        state["summary"] = summarize(state, None)
        write_json(path, state)
        return state
    _migrate(state)
    activation = state["activation"]
    if activation["model_run_id"] != report["id"] or activation["artifact_sha256"] != hashes:
        raise ValueError("Frozen forward model changed; start a separately named forward cohort")
    activation.setdefault("contract_version", CONTRACT_VERSION)
    activation.setdefault("contract_sha256", contract_hashes)
    activation.setdefault("economics", risk)
    if activation["contract_sha256"] != contract_hashes:
        raise ValueError("Forward contract code changed; create a separately named forward cohort")
    if activation["contract_version"] != CONTRACT_VERSION:
        raise ValueError(
            "Forward outcome contract changed; create a separately named forward cohort"
        )
    economics = activation["economics"]
    costs = EquityCostModel(ChargeSchedule.model_validate(economics["costs"]))
    existing = {row["signal_id"] for row in state["records"]}
    slippage = float(costs.slippage_pct)
    holidays, calendar_hash = _calendar(root)
    errors = []
    db = root / "data/tradedesk.duckdb"
    cache: dict[str, pd.DataFrame] = {}
    with duckdb.connect(str(db), read_only=True) as con:
        benchmark, sector_of, sector_codes = _instrument_maps(con, root)

        def bars(code: str) -> pd.DataFrame:
            if code not in cache:
                cache[code] = _closed_bars(_load_bars(con, code), as_of)
            return cache[code]

        for source, watchlist in watchlists:
            if watchlist["on"] <= activation["forward_after"]:
                continue
            armed = date.fromisoformat(watchlist["on"])
            if as_of < datetime.combine(armed, daytime(15, 30), tzinfo=IST):
                continue
            nifty = _returns(bars(benchmark), armed) if benchmark else (None, None)
            for entry in watchlist.get("entries", []):
                signal_id = entry.get("signal", {}).get("id")
                if not signal_id or signal_id in existing:
                    continue
                try:
                    if entry["signal"]["armed_on"] != watchlist["on"]:
                        raise ValueError("Signal arming date does not match its watchlist date")
                    code = entry["signal"]["scrip_code"]
                    sector_name = sector_of.get(code)
                    sector_code = sector_codes.get(sector_name) if sector_name else None
                    context = _returns(bars(sector_code), armed) if sector_code else (None, None)
                    common = next(iter(bundles.values())).features
                    values = feature_values(
                        entry, watchlist, bars(code), common, nifty=nifty, sector=context
                    )
                    state["records"].append(
                        _score(
                            entry,
                            watchlist,
                            source,
                            bundles,
                            bars(code),
                            values,
                            report,
                            activation,
                            None,
                            holidays,
                            calendar_hash,
                        )
                    )
                    existing.add(signal_id)
                except Exception as exc:
                    errors.append({"signal_id": signal_id, "error": f"{type(exc).__name__}: {exc}"})
        for record in state["records"]:
            try:
                sessions = list(bars(benchmark).index.date) if benchmark else None
                resolve_record(
                    record,
                    bars(record["scrip_code"]),
                    slippage,
                    costs=costs,
                    session_dates=sessions,
                )
            except Exception as exc:
                errors.append(
                    {"signal_id": record["signal_id"], "error": f"{type(exc).__name__}: {exc}"}
                )
    latest_candle = max(
        (str(frame.index[-1].date()) for frame in cache.values() if not frame.empty), default=None
    )
    state["records"].sort(key=lambda row: (row["armed_on"], row["signal_id"]))
    timestamped = [{**error, "at": now()} for error in errors]
    state["recent_errors"] = (state.get("recent_errors", []) + timestamped)[-100:]
    state["current_errors"] = timestamped
    state["summary"] = summarize(state, latest_candle)
    write_json(path, state)
    return state


def watch(interval_seconds: int, root: Path = ROOT, output: Path = OUTPUT) -> None:
    if interval_seconds < 60:
        raise ValueError("Forward polling interval must be at least 60 seconds")
    with _lock(output, "watcher"):
        status_path = output / "forward/watcher_status.json"
        status = _read(status_path) if status_path.exists() else {}
        status.update(pid=os.getpid(), started_at=now(), interval_seconds=interval_seconds)
        try:
            while True:
                status.update(status="collecting", heartbeat_at=now())
                write_json(status_path, status)
                try:
                    state = collect(root, output)
                    summary = state["summary"]
                    status.update(
                        status="waiting",
                        last_success_at=now(),
                        current_error=None,
                        consecutive_failures=0,
                        model_run_id=state["activation"]["model_run_id"],
                    )
                    if state.get("current_errors"):
                        status["last_error"] = state["current_errors"][-1]
                        status["current_error"] = status["last_error"]
                        status["status"] = "degraded"
                    print(
                        f"Forward: {summary['total_scored']} scored, "
                        f"{summary['all_scored']['resolved']} resolved",
                        flush=True,
                    )
                except Exception as exc:
                    error = {"at": now(), "error": f"{type(exc).__name__}: {exc}"}
                    status.update(
                        status="error",
                        current_error=error,
                        last_error=error,
                        consecutive_failures=status.get("consecutive_failures", 0) + 1,
                    )
                    print(f"Forward collection failed: {error['error']}", flush=True)
                deadline = time.monotonic() + interval_seconds
                status["next_collection_at"] = (
                    datetime.now(UTC) + timedelta(seconds=interval_seconds)
                ).isoformat()
                while True:
                    status["heartbeat_at"] = now()
                    write_json(status_path, status)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(60, remaining))
        finally:
            status.update(status="stopped", heartbeat_at=now())
            write_json(status_path, status)
