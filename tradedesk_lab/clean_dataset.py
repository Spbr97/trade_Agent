"""Separate, auditable historical dataset; never rewrites production or legacy exports.

The source is a pool of previously triggered historical candidates, not a complete
point-in-time scanner replay. Rebuilding labels/features cannot remove that selection
bias, survivorship bias, or turn an inspected history into fresh evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import duckdb
import joblib
import numpy as np
import pandas as pd

from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.signals import ExitPlan
from tradedesk.markets.market import nse_market
from tradedesk.prediction.features import FEATURE_NAMES, signal_features
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.dataset import Dataset, signal
from tradedesk_lab.outcomes import CONTRACT_VERSION, geometry_error, simulate_outcome

EXCLUDED_FEATURES = {
    "rs_percentile",
    "sector_return_1d",
    "sector_return_5d",
    "stop_atr",
    "room_r",
    "base_depth",
    "base_contraction",
    "base_dryup",
    "base_tests",
    "breadth_pct",
    "regime_risk_on",
    "regime_risk_off",
    "sessions_to_results",
    "expiry_week",
}
FEATURES = [name for name in FEATURE_NAMES if name not in EXCLUDED_FEATURES]
FEATURE_VERSION = "arming-candle-context-v1"


def load_prepared(root: Path = ROOT, output: Path = OUTPUT) -> Dataset:
    """Load only the explicit trusted local snapshot, refusing changed contracts/inputs."""
    pointer = json.loads((output / "reliability/dataset-latest.json").read_text("utf-8"))
    identifier = pointer["id"]
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid clean dataset identifier")
    folder = output / "reliability/datasets" / identifier
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    expected = {
        "geometry_sha256": root / "data/reports/barrier_signals.csv",
        "config_sha256": root / "config/risk.yaml",
        "universe_sha256": root / "config/universe.yaml",
        "builder_sha256": Path(__file__),
        "outcomes_sha256": Path(__file__).with_name("outcomes.py"),
    }
    if manifest.get("label_version") != CONTRACT_VERSION or any(
        manifest.get(key) != digest(path) for key, path in expected.items()
    ):
        raise ValueError("Prepared data contract/inputs changed; run prepare-clean again")
    return joblib.load(folder / "dataset.joblib")


def load_bars(con, code: str, through: pd.Timestamp) -> pd.DataFrame:
    data = con.execute(
        "SELECT ts,open,high,low,close,volume FROM candles "
        "WHERE scrip_code=? AND interval='1day' ORDER BY ts",
        [code],
    ).df()
    data.index = (
        pd.to_datetime(data.pop("ts"), unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
        .dt.normalize()
    )
    data = data.loc[data.index <= through]
    if data.index.has_duplicates:
        raise ValueError("duplicate_session")
    return data


def valid_bars(data: pd.DataFrame) -> bool:
    if data.empty:
        return False
    prices = data[["open", "high", "low", "close"]]
    return bool(
        np.isfinite(prices.to_numpy()).all()
        and (prices > 0).all().all()
        and np.isfinite(data.volume).all()
        and (data.volume >= 0).all()
        and (data.high >= prices[["open", "close", "low"]].max(axis=1)).all()
        and (data.low <= prices[["open", "close", "high"]].min(axis=1)).all()
    )


def point_in_time_features(sig, features, benchmark, vix) -> dict[str, float]:
    """Explicitly slice every input at the arming close, even when future rows exist."""
    on = pd.Timestamp(sig.armed_on)
    history = features.loc[:on]
    reference = benchmark.loc[:on, "close"]
    volatility = vix.loc[:on, "close"]
    if history.empty or history.index[-1] != on:
        raise ValueError("missing_arming_candle")
    if len(reference) < 6 or reference.index[-1] != on:
        raise ValueError("missing_or_stale_benchmark")
    if len(volatility) < 6 or volatility.index[-1] != on:
        raise ValueError("missing_or_stale_vix")
    values = signal_features(
        sig,
        history,
        nifty_return_1d=float((reference.iloc[-1] / reference.iloc[-2] - 1) * 100),
        nifty_return_5d=float((reference.iloc[-1] / reference.iloc[-6] - 1) * 100),
        vix=float(volatility.iloc[-1]),
        vix_change_5d=float((volatility.iloc[-1] / volatility.iloc[-6] - 1) * 100),
    )
    result = {name: values[name] for name in FEATURES}
    if not np.isfinite(list(result.values())).all():
        raise ValueError("nonfinite_features")
    return result


def prepare_clean(root: Path = ROOT, output: Path = OUTPUT) -> Dataset:
    geometry = root / "data/reports/barrier_signals.csv"
    source = pd.read_csv(geometry, dtype={"scrip_code": str, "signal_id": str})
    if source.signal_id.duplicated().any():
        raise ValueError("Duplicate historical signal identifiers")
    source["armed_on"] = pd.to_datetime(source.armed_on)
    source["entry_date"] = pd.to_datetime(source.entry_date)
    # No old exported labels, scores, geometry-derived features, or regime assignments.
    source["regime"] = "unclassified"
    settings = load_config(root)
    market = nse_market(settings)
    clock = datetime.now(ZoneInfo("Asia/Kolkata"))
    through = pd.Timestamp(clock.date())
    if (clock.hour, clock.minute) < (15, 45):
        through -= pd.Timedelta(days=1)
    rejected: Counter = Counter()
    exclusions, records, bars = [], [], {}
    candle_hash = hashlib.sha256()

    def reject(row, reason):
        rejected[reason] += 1
        exclusions.append({"signal_id": str(row.signal_id), "reason": reason})

    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        indices = {
            str(name).upper(): str(code)
            for code, name in con.execute(
                "SELECT scrip_code,trading_symbol FROM instruments "
                "WHERE exch='NSE' AND kind='index'"
            ).fetchall()
        }
        benchmark = load_bars(con, indices[settings.universe.benchmark.upper()], through)
        vix = load_bars(con, indices[settings.universe.volatility_index.upper()], through)
        if not valid_bars(benchmark) or not valid_bars(vix):
            raise ValueError("Missing or invalid benchmark/VIX history")
        calendar = benchmark.index
        for context in (benchmark, vix):
            candle_hash.update(pd.util.hash_pandas_object(context, index=True).values.tobytes())
        for number, (code, group) in enumerate(source.groupby("scrip_code", sort=True)):
            try:
                data = load_bars(con, str(code), through)
            except ValueError as exc:
                for _, row in group.iterrows():
                    reject(row, str(exc))
                continue
            if not valid_bars(data):
                for _, row in group.iterrows():
                    reject(row, "missing_or_invalid_ohlcv")
                continue
            # Benchmark-defined sessions; phantom non-session rows never shift expiry.
            data = data.loc[data.index.isin(calendar)]
            if data.empty:
                for _, row in group.iterrows():
                    reject(row, "no_exchange_sessions")
                continue
            features = daily_features(data)
            data = features[["open", "high", "low", "close", "volume", "ema10", "atr14"]]
            bars[str(code)] = data
            candle_hash.update(str(code).encode())
            candle_hash.update(pd.util.hash_pandas_object(data, index=True).values.tobytes())
            turnover = (data.close * data.volume).rolling(20, min_periods=20).mean()
            for _, row in group.iterrows():
                error = geometry_error(float(row.entry), float(row.stop), float(row.t1))
                if error:
                    reject(row, error)
                    continue
                if row.armed_on not in calendar or row.entry_date not in data.index:
                    reject(row, "missing_session")
                    continue
                age = calendar.get_loc(row.entry_date) - calendar.get_loc(row.armed_on)
                if not 1 <= age <= 3:
                    reject(row, "entry_outside_validity")
                    continue
                history = data.loc[: row.armed_on]
                if len(history) < 250 or history.index[-1] != row.armed_on:
                    reject(row, "insufficient_or_stale_warmup")
                    continue
                if float(history.close.iloc[-1]) < float(settings.universe.min_price):
                    reject(row, "price_floor")
                    continue
                if turnover.loc[row.armed_on] < float(settings.universe.min_avg_daily_turnover_inr):
                    reject(row, "turnover_floor")
                    continue
                atr_fraction = float(features.loc[row.armed_on, "atr_pct"]) / 100
                band = settings.universe.atr_pct_band
                if not float(band.min) <= atr_fraction <= float(band.max):
                    reject(row, "atr_band")
                    continue
                sig = signal(row).model_copy(
                    update={
                        "exit_plan": ExitPlan(
                            partial_fraction=1.0,
                            time_stop_sessions=settings.risk.time_stop.sessions,
                            time_stop_min_r=float(settings.risk.time_stop.min_r),
                            max_hold_sessions=settings.risk.max_hold_sessions,
                        )
                    }
                )
                try:
                    values = point_in_time_features(sig, features, benchmark, vix)
                except ValueError as exc:
                    reject(row, str(exc))
                    continue
                # Saved fills already include modeled entry slippage. Check plausibility,
                # but do not pretend an original trigger can be reconstructed from a fill.
                entry_bar = data.loc[row.entry_date]
                unadjusted_fill = float(row.entry) / (1 + float(market.costs.slippage_pct))
                if (
                    not float(entry_bar.low) - 0.01
                    <= unadjusted_fill
                    <= float(entry_bar.high) + 0.01
                ):
                    reject(row, "saved_fill_outside_bar")
                    continue
                size = position_size(
                    SizeInputs(
                        equity=float(settings.risk.trading_capital),
                        entry=float(row.entry),
                        stop=float(row.stop),
                        max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
                        max_position_value_pct=float(settings.risk.max_position_value_pct),
                        size_multiplier=float(settings.risk.regime_size_multiplier.neutral),
                        gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
                        gap95_pct=gap95_pct(history),
                        available_heat_pct=float(settings.risk.max_portfolio_heat_pct),
                    )
                )
                if not size.viable:
                    reject(row, "unsizeable")
                    continue
                end = calendar.get_loc(row.entry_date) + settings.risk.max_hold_sessions + 1
                expected = calendar[calendar.get_loc(row.entry_date) : end]
                if not expected.isin(data.index).all():
                    reject(row, "missing_holding_window_candle")
                    continue
                outcome = simulate_outcome(
                    sig,
                    data,
                    row.entry_date,
                    float(row.entry),
                    size.qty,
                    market.costs,
                )
                if outcome["status"] != "resolved":
                    reject(row, outcome["status"])
                    continue
                record = (
                    row.to_dict()
                    | values
                    | {
                        k: outcome[k]
                        for k in (
                            "label",
                            "target_hit",
                            "gross_r",
                            "net_r",
                            "net_pnl",
                            "costs",
                            "outcome",
                        )
                    }
                )
                record.update(
                    label_end_date=pd.Timestamp(outcome["label_end_date"]),
                    trade_end_date=pd.Timestamp(outcome["label_end_date"]),
                    qty=size.qty,
                )
                records.append(record)
            if number % 100 == 0:
                print(f"Clean dataset: {number + 1} symbols; {len(records)} valid rows", flush=True)
    if not records:
        raise ValueError(f"No auditable resolved rows: {dict(rejected)}")
    frame = pd.DataFrame(records).sort_values(["armed_on", "signal_id"]).reset_index(drop=True)
    identifier = uuid4().hex
    manifest = {
        "id": identifier,
        "label_version": CONTRACT_VERSION,
        "feature_version": FEATURE_VERSION,
        "market": "nse",
        "historical_research": True,
        "source_rows": len(source),
        "rows": len(frame),
        "symbols": int(frame.scrip_code.nunique()),
        "from": str(frame.armed_on.min().date()),
        "to": str(frame.armed_on.max().date()),
        "geometry_sha256": digest(geometry),
        "config_sha256": digest(root / "config/risk.yaml"),
        "universe_sha256": digest(root / "config/universe.yaml"),
        "builder_sha256": digest(Path(__file__)),
        "outcomes_sha256": digest(Path(__file__).with_name("outcomes.py")),
        "candles_sha256": candle_hash.hexdigest(),
        "closed_bars_through": str(through.date()),
        "excluded": dict(rejected),
        "excluded_features": sorted(EXCLUDED_FEATURES),
        "label_rate": float(frame.label.mean()),
        "mean_candidate_net_r": float(frame.net_r.mean()),
        "notes": [
            "Previously inspected historical diagnostic; not untouched or prospective evidence.",
            "Source includes only previously triggered candidates, not all scanner opportunities.",
            "Saved fills cannot reconstruct original triggers or prove intraday confirmation.",
            "Unknown entry-day ordering: target requires closing confirmation; stop wins ties.",
            "Unverified regime is unclassified; sizing uses the conservative neutral multiplier.",
            "Regime/results/surveillance and portfolio gates are not established here.",
            "Candidate outcomes are not portfolio returns or executable call availability.",
            "Current broker universe and adjusted history retain survivorship/revision bias.",
            "Intraday charges are provisional; historical fees use current configured rates.",
            "Model predictors exclude saved-fill geometry and unverified legacy context.",
        ],
    }
    dataset = Dataset(frame, FEATURES, calendar, bars, manifest)
    folder = output / "reliability/datasets" / identifier
    folder.mkdir(parents=True, exist_ok=False)
    joblib.dump(dataset, folder / "dataset.joblib")
    write_json(folder / "manifest.json", manifest)
    write_json(folder / "exclusions.json", exclusions)
    write_json(output / "reliability/dataset-latest.json", {"id": identifier})
    print(
        f"Clean dataset complete: {len(frame)} rows; {len(source) - len(frame)} excluded",
        flush=True,
    )
    return dataset
