import json
from pathlib import Path

import numpy as np
import pandas as pd
import tradedesk_lab.osr_development_experiment as mod
from tradedesk_lab.artifacts import digest
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import EquityCostModel

SESSIONS = [f"2026-01-{5 + index:02d}" for index in range(6)]
CODES = ["NSE_MAIN", "NSE_P1", "NSE_P2", "NSE_P3", "NSE_P4", "NSE_P5"]


def _flat_session(session: str, *, periods: int = 90, close: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(f"{session} 09:15", periods=periods, freq="1min", tz="Asia/Kolkata")
    closes = np.full(periods, close)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.02,
            "low": closes - 0.02,
            "close": closes,
            "volume": np.full(periods, 1_000.0),
        },
        index=idx,
    )


def _gap_reclaim_session(session: str, *, periods: int = 90) -> pd.DataFrame:
    frame = _flat_session(session, periods=periods, close=97.9)
    frame.loc[frame.index[0], ["open", "high", "low", "close"]] = [
        98.0,
        98.05,
        97.85,
        97.9,
    ]
    frame.loc[frame.index[8], ["open", "high", "low", "close"]] = [
        97.8,
        97.85,
        97.6,
        97.7,
    ]
    frame.loc[frame.index[14], ["open", "high", "low", "close"]] = [
        97.8,
        97.9,
        97.7,
        97.85,
    ]
    frame.loc[frame.index[15], ["open", "high", "low", "close", "volume"]] = [
        97.85,
        98.20,
        97.80,
        98.15,
        2_000.0,
    ]
    frame.loc[frame.index[16] :, ["open", "high", "low", "close"]] = [
        98.1,
        98.2,
        98.0,
        98.1,
    ]
    return frame


def _daily_frame(n: int = 90, *, end: str = "2026-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=n, tz="Asia/Kolkata")
    closes = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(n, 200_000.0),
        },
        index=idx,
    )


class FakeContract:
    sha256 = "source-contract-hash"


class FakeRisk:
    trading_capital = 100_000.0


class FakeSettings:
    risk = FakeRisk()


class FakeMarket:
    costs = EquityCostModel(ChargeSchedule())


def _sources(*, include_all_peers: bool = True) -> dict:
    per_code_sessions: dict[str, dict[str, pd.DataFrame]] = {}
    daily: dict[str, pd.DataFrame] = {}
    included_sessions: dict[str, set[str]] = {}
    for code in CODES:
        frames = {
            session: _flat_session(session)
            for session in SESSIONS[:-1]
        }
        frames[SESSIONS[-1]] = (
            _gap_reclaim_session(SESSIONS[-1])
            if code == "NSE_MAIN"
            else _flat_session(SESSIONS[-1])
        )
        per_code_sessions[code] = frames
        daily[code] = _daily_frame()
        # The first five sessions are causal RVOL history; only the final session
        # belongs to this compact synthetic evaluation population.
        included_sessions[code] = {SESSIONS[-1]}
    if not include_all_peers:
        included_sessions.pop("NSE_P5")
    return {
        "audit_id": "audit-1",
        "dataset_id": "dataset-1",
        "contract": FakeContract(),
        "settings": FakeSettings(),
        "market": FakeMarket(),
        "daily": daily,
        "symbols": {code: code for code in CODES},
        "included_sessions": included_sessions,
        "per_code_sessions": per_code_sessions,
        "per_code_daily_dates": {
            code: pd.DatetimeIndex(frame.index).date for code, frame in daily.items()
        },
        "source_sha256": "source-hash",
    }


def test_build_dataset_finds_features_and_resolves_real_osr_opportunity(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "load_real_session_sources", lambda *args, **kwargs: _sources())

    frame, excluded, metadata = mod.build_development_dataset(
        root=tmp_path,
        output=tmp_path / "lab",
    )

    assert metadata["opportunities_found"] >= 1
    assert metadata["resolved_rows"] == len(frame)
    assert metadata["evidence_class"] == "consumed_historical_development"
    assert len(frame) >= 1
    assert set(frame["scrip_code"]) == {"NSE_MAIN"}
    assert set(frame["geometry_id"]).issubset(
        {geometry.id for geometry in DEFAULT_OSR_CONTRACT.geometries}
    )
    assert set(frame["label"]).issubset({0, 1})
    assert {feature.name for feature in DEFAULT_OSR_CONTRACT.features}.issubset(frame.columns)
    assert frame["net_r"].apply(np.isfinite).all()
    assert isinstance(excluded, list)


def test_build_dataset_records_feature_failure_with_too_few_peers(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mod,
        "load_real_session_sources",
        lambda *args, **kwargs: _sources(include_all_peers=False),
    )

    frame, excluded, metadata = mod.build_development_dataset(
        root=tmp_path,
        output=tmp_path / "lab",
    )

    assert frame.empty
    assert metadata["opportunities_found"] >= 1
    assert any("insufficient_peer_universe" in row["reason"] for row in excluded)


def test_empty_dataset_keeps_a_stable_schema():
    frame = mod._empty_dataset()
    assert list(frame.columns[:30]) == [
        feature.name for feature in DEFAULT_OSR_CONTRACT.features
    ]
    assert {"label", "net_r", "mode", "geometry_id"}.issubset(frame.columns)


def test_freeze_dataset_writes_consumed_development_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "load_real_session_sources", lambda *args, **kwargs: _sources())

    report = mod.freeze_development_dataset(root=tmp_path, output=tmp_path / "lab")

    assert report["status"] == "development_dataset_frozen_not_evaluated"
    assert report["milestone"] == 2
    assert report["evidence_class"] == "consumed_historical_development"
    assert report["baseline_improved"] is False
    assert report["decision"]["change_live_behavior"] is False
    run_dir = tmp_path / f"lab/osr/development_dataset/runs/{report['id']}"
    assert (run_dir / "events.csv").is_file()
    assert (run_dir / "excluded_events.json").is_file()
    assert len(pd.read_csv(run_dir / "events.csv")) == report["resolved_rows"]
    latest = json.loads((tmp_path / "lab/osr/development_dataset/latest.json").read_text())
    assert latest["id"] == report["id"]


def test_fixed_risk_quantity_is_reused_without_weakening_risk():
    assert mod.fixed_risk_quantity(100.0, 99.0, equity=100_000.0, risk_pct=0.005) == 500
    assert mod.fixed_risk_quantity(100.0, 100.0, equity=100_000.0, risk_pct=0.005) == 0


def test_tracked_osr_dataset_assembly_evidence_matches_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/osr-features.json").read_text(encoding="utf-8"))

    assert evidence["development_dataset_assembly_implemented"] is True
    assert evidence["real_run_completed"] is False
    assert evidence["dataset_implementation_sha256"] == digest(Path(mod.__file__))
    assert evidence["dataset_tests_sha256"] == digest(
        root / "tests_lab/test_osr_development_experiment.py"
    )
    assert evidence["decision"]["change_canonical_baseline"] is False
