"""Prospective shadow evidence: frozen scores, later outcomes, no production writes."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

from tradedesk.backtest.fills import Bar, EntryOutcome, evaluate_entry
from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.signals import Signal
from tradedesk.markets.market import nse_market
from tradedesk.prediction.features import signal_features
from tradedesk.prediction.labeling import triple_barrier
from tradedesk.reliability import wilson_lower_bound
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.portable import load_portable
from tradedesk_lab.registry import Registry

STATE_VERSION = 1


def now() -> str:
    return datetime.now(UTC).isoformat()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _watchlists(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    result = []
    for path in sorted((root / "data/watchlists").glob("*.json")):
        try:
            value = _read(path)
            date.fromisoformat(value["on"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        result.append((path, value))
    return result


def _load_frozen(output: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    latest = _read(output / "latest.json")["id"]
    folder = output / "runs" / latest
    report = _read(folder / "report.json")
    with Registry(output / "registry.sqlite", readonly=True) as registry:
        run = registry.run(latest)
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
        sector: indices[sector.upper()]
        for sector in membership
        if sector.upper() in indices
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


def _feature_hash(values: dict[str, float | None]) -> str:
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
) -> dict[str, Any]:
    frame = pd.DataFrame([values], columns=next(iter(bundles.values())).features)
    probabilities = {family: float(bundle.predict(frame)[0]) for family, bundle in bundles.items()}
    values_array = np.array(list(probabilities.values()), dtype=float)
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
    return {
        "signal_id": signal["id"],
        "source_watchlist": path.name,
        "source_sha256": digest(path),
        "armed_on": signal["armed_on"],
        "scored_at": now(),
        "symbol": signal["symbol"],
        "scrip_code": signal["scrip_code"],
        "setup": signal["setup"],
        "signal": signal,
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
        "resolved_at": None,
        "last_evaluated_candle": str(bars.index[-1].date()) if len(bars) else None,
    }


def resolve_record(record: dict[str, Any], bars: pd.DataFrame, slippage_pct: float) -> bool:
    """Advance one frozen record with the base daily entry and barrier rules."""
    if record["status"] in {"resolved", "chased", "invalidated", "expired"}:
        return False
    signal = Signal.model_validate(record["signal"])
    future = daily_features(bars.loc[bars.index.date > signal.armed_on])
    before = json.dumps(record, sort_keys=True, default=str)
    record["last_evaluated_candle"] = str(bars.index[-1].date()) if len(bars) else None
    for session, (stamp, row) in enumerate(future.iterrows(), start=1):
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
            record.update(entry_date=str(stamp.date()), fill_price=float(fill))
            label = triple_barrier(
                future.loc[stamp:],
                entry=float(fill),
                stop=signal.stop,
                target=signal.t1,
                max_hold=signal.exit_plan.max_hold_sessions,
            )
            if label.outcome == "insufficient":
                record["status"] = "triggered_pending"
            else:
                risk = float(fill) - signal.stop
                gross_r = (float(label.exit_price) - float(fill)) / risk if risk > 0 else None
                record.update(
                    status="resolved",
                    outcome=label.outcome,
                    label=label.label,
                    sessions_to_outcome=label.sessions,
                    exit_price=label.exit_price,
                    gross_r=gross_r,
                    resolved_at=now(),
                )
            break
        if session > signal.valid_sessions:
            record.update(status="expired", outcome="expired", resolved_at=now())
            break
    return before != json.dumps(record, sort_keys=True, default=str)


def summarize(state: dict[str, Any], latest_candle: str | None) -> dict[str, Any]:
    records = state["records"]
    resolved = [row for row in records if row["status"] == "resolved"]
    selected = [row for row in records if row["selected_for_research"]]
    selected_done = [row for row in selected if row["status"] == "resolved"]

    def evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(row["label"] == 1 for row in rows)
        return {
            "resolved": len(rows),
            "hits": wins,
            "hit_rate": wins / len(rows) if rows else None,
            "wilson_lower": float(wilson_lower_bound(wins, len(rows))) if rows else None,
            "mean_gross_r": float(np.mean([row["gross_r"] for row in rows])) if rows else None,
        }

    by_setup = {
        setup: evidence([row for row in resolved if row["setup"] == setup])
        for setup in sorted({row["setup"] for row in records})
    }
    return {
        "total_scored": len(records),
        "pending_entry": sum(row["status"] == "pending_entry" for row in records),
        "triggered_pending": sum(row["status"] == "triggered_pending" for row in records),
        "untriggered_terminal": sum(
            row["status"] in {"chased", "invalidated", "expired"} for row in records
        ),
        "all_scored": evidence(resolved),
        "selected_calls": len(selected),
        "selected": evidence(selected_done),
        "by_setup": by_setup,
        "latest_candle": latest_candle,
        "minimum_early_read": 30,
        "minimum_oos_evidence": 100,
        "production_minimum_per_setup": 500,
        "updated_at": now(),
    }


def collect(root: Path = ROOT, output: Path = OUTPUT) -> dict[str, Any]:
    """Score only post-activation watchlists, then resolve frozen records idempotently."""
    report, bundles, hashes = _load_frozen(output)
    path = output / "forward/state.json"
    watchlists = _watchlists(root)
    if not path.exists():
        latest_existing = max(
            (value["on"] for _, value in watchlists), default=report["metadata"]["to"]
        )
        state = {
            "version": STATE_VERSION,
            "activation": {
                "activated_at": now(),
                "model_run_id": report["id"],
                "model_data_through": report["metadata"]["to"],
                "forward_after": latest_existing,
                "artifact_sha256": hashes,
                "rule": "Only watchlists dated after activation baseline count as forward evidence",
            },
            "records": [],
            "recent_errors": [],
        }
        state["summary"] = summarize(state, None)
        write_json(path, state)
        return state
    state = _read(path)
    activation = state["activation"]
    if activation["model_run_id"] != report["id"] or activation["artifact_sha256"] != hashes:
        raise ValueError("Frozen forward model changed; start a separately named forward cohort")
    existing = {row["signal_id"] for row in state["records"]}
    settings = load_config(root)
    slippage = float(nse_market(settings).costs.slippage_pct)
    errors = []
    db = root / "data/tradedesk.duckdb"
    cache: dict[str, pd.DataFrame] = {}
    with duckdb.connect(str(db), read_only=True) as con:
        benchmark, sector_of, sector_codes = _instrument_maps(con, root)

        def bars(code: str) -> pd.DataFrame:
            if code not in cache:
                cache[code] = _load_bars(con, code)
            return cache[code]

        for source, watchlist in watchlists:
            if watchlist["on"] <= activation["forward_after"]:
                continue
            armed = date.fromisoformat(watchlist["on"])
            nifty = _returns(bars(benchmark), armed) if benchmark else (None, None)
            for entry in watchlist.get("entries", []):
                signal_id = entry.get("signal", {}).get("id")
                if not signal_id or signal_id in existing:
                    continue
                try:
                    code = entry["signal"]["scrip_code"]
                    sector_name = sector_of.get(code)
                    sector_code = sector_codes.get(sector_name) if sector_name else None
                    context = _returns(bars(sector_code), armed) if sector_code else (None, None)
                    common = next(iter(bundles.values())).features
                    values = feature_values(
                        entry, watchlist, bars(code), common, nifty=nifty, sector=context
                    )
                    state["records"].append(
                        _score(entry, watchlist, source, bundles, bars(code), values, report)
                    )
                    existing.add(signal_id)
                except Exception as exc:
                    errors.append({"signal_id": signal_id, "error": f"{type(exc).__name__}: {exc}"})
        for record in state["records"]:
            try:
                resolve_record(record, bars(record["scrip_code"]), slippage)
            except Exception as exc:
                errors.append(
                    {"signal_id": record["signal_id"], "error": f"{type(exc).__name__}: {exc}"}
                )
        latest = con.execute(
            "SELECT max(to_timestamp(ts)) FROM candles WHERE interval='1day'"
        ).fetchone()[0]
    latest_candle = str(pd.Timestamp(latest).tz_convert("Asia/Kolkata").date()) if latest else None
    state["records"].sort(key=lambda row: (row["armed_on"], row["signal_id"]))
    state["recent_errors"] = errors[-100:]
    state["summary"] = summarize(state, latest_candle)
    write_json(path, state)
    return state


def watch(interval_seconds: int, root: Path = ROOT, output: Path = OUTPUT) -> None:
    if interval_seconds < 60:
        raise ValueError("Forward polling interval must be at least 60 seconds")
    while True:
        try:
            state = collect(root, output)
            summary = state["summary"]
            print(
                f"Forward: {summary['total_scored']} scored, "
                f"{summary['all_scored']['resolved']} resolved",
                flush=True,
            )
        except Exception as exc:
            print(f"Forward collection failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(interval_seconds)
