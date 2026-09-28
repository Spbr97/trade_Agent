import json

import numpy as np
import pandas as pd
import pytest
import tradedesk_lab.aem_v2_development_experiment as mod
from tradedesk_lab.aem_v2_development_experiment import _session_slices, fixed_risk_quantity

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import EquityCostModel

SESSIONS = [f"2026-01-{5 + i:02d}" for i in range(6)]  # 2026-01-05 .. 2026-01-10
CODES = ["NSE_MAIN", "NSE_P1", "NSE_P2", "NSE_P3", "NSE_P4", "NSE_P5"]


def testfixed_risk_quantity():
    assert fixed_risk_quantity(100.0, 99.0, equity=100_000.0, risk_pct=0.005) == 500
    assert fixed_risk_quantity(100.0, 100.0, equity=100_000.0, risk_pct=0.005) == 0
    assert fixed_risk_quantity(100.0, 101.0, equity=100_000.0, risk_pct=0.005) == 0


def _flat_session(session: str, *, periods: int = 90, close: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(f"{session} 09:15", periods=periods, freq="1min", tz="Asia/Kolkata")
    closes = np.full(periods, close)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.02,
            "low": closes - 0.02,
            "close": closes,
            "volume": np.full(periods, 100.0),
        },
        index=idx,
    )


def _impulse_session(session: str, *, suffix_bars: int = 65) -> pd.DataFrame:
    """Ten flat bars, then a real (previously verified) anticipatory-impulse trigger,
    then enough flat bars for every geometry's max-hold window (up to 60 minutes) to
    resolve cleanly - with a few minutes of slack so a one-bar-delay execution stress
    still has real bars to resolve against.
    """
    prefix_closes = [100.0] * 10
    impulse_closes = [100.0, 100.02, 100.05, 100.06, 100.10, 100.20]
    impulse_highs = [100.02, 100.04, 100.07, 100.65, 100.12, 100.22]
    impulse_opens = [100.0, *impulse_closes[:-1]]
    impulse_lows = [
        min(o, c) - 0.02 for o, c in zip(impulse_opens, impulse_closes, strict=True)
    ]
    suffix_closes = [100.20] * suffix_bars
    closes = prefix_closes + impulse_closes + suffix_closes
    highs = [100.02] * 10 + impulse_highs + [100.22] * suffix_bars
    lows = [99.98] * 10 + impulse_lows + [100.18] * suffix_bars
    volumes = [100.0] * 10 + [100, 100, 100, 100, 100, 220] + [100.0] * suffix_bars
    idx = pd.date_range(f"{session} 09:15", periods=len(closes), freq="1min", tz="Asia/Kolkata")
    opens = [closes[0], *closes[:-1]]
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}, index=idx
    )


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


def _build_environment(*, include_all_peers: bool = True):
    minute: dict[str, pd.DataFrame] = {}
    daily: dict[str, pd.DataFrame] = {}
    symbols: dict[str, str] = {}
    for code in CODES:
        sessions = [_flat_session(s) for s in SESSIONS[:-1]]
        last = _impulse_session(SESSIONS[-1]) if code == "NSE_MAIN" else _flat_session(SESSIONS[-1])
        minute[code] = pd.concat([*sessions, last])
        daily[code] = _daily_frame()
        symbols[code] = code

    included_rows = []
    for code in CODES:
        peers_include = CODES if include_all_peers else [c for c in CODES if c != "NSE_P1"]
        if code not in peers_include and code != "NSE_MAIN":
            continue
        for session in SESSIONS:
            included_rows.append({"scrip_code": code, "session": session})
    included = pd.DataFrame(included_rows)
    return daily, minute, symbols, included


class FakeContract:
    sha256 = "contract-hash"


class FakeUniverse:
    benchmark = "NIFTY 50"


class FakeRisk:
    trading_capital = 100_000.0


class FakeSettings:
    universe = FakeUniverse()
    risk = FakeRisk()


class FakeMarket:
    costs = EquityCostModel(ChargeSchedule())


def _patch_common(monkeypatch, daily, minute, symbols, included, *, source_sha="abc"):
    def fake_load_universe_audit(output, audit_id):
        report = {
            "id": "audit-1",
            "source_dataset_id": "dataset-1",
            "contract_sha256": "contract-hash",
            "source_sha256": source_sha,
        }
        return "audit-1", report, included

    def fake_load_v1_manifest(output, dataset_id):
        return {"plan_id": "plan-1"}, FakeContract()

    def fake_read_staged_aem_source(root, output, *, plan_id, contract, benchmark_symbol):
        source = {"sha256": "abc", "coverage": {}}
        return daily, minute, symbols, [], [], source

    monkeypatch.setattr(mod, "_load_universe_audit", fake_load_universe_audit)
    monkeypatch.setattr(mod, "_load_v1_manifest", fake_load_v1_manifest)
    monkeypatch.setattr(mod, "read_staged_aem_source", fake_read_staged_aem_source)
    monkeypatch.setattr(mod, "load_config", lambda root: FakeSettings())
    monkeypatch.setattr(mod, "nse_market", lambda settings: FakeMarket())


def test_session_slices_groups_by_calendar_date():
    frame = pd.concat([_flat_session(s, periods=5) for s in SESSIONS[:3]])
    slices = _session_slices(frame)
    assert set(slices) == set(SESSIONS[:3])
    assert all(len(v) == 5 for v in slices.values())


def test_build_development_dataset_finds_and_resolves_a_real_opportunity(monkeypatch, tmp_path):
    daily, minute, symbols, included = _build_environment()
    _patch_common(monkeypatch, daily, minute, symbols, included)

    frame, excluded, metadata = mod.build_development_dataset(
        root=tmp_path, output=tmp_path / "lab"
    )

    assert metadata["opportunities_found"] >= 1
    assert metadata["resolved_rows"] == len(frame)
    assert len(frame) >= 1
    assert set(frame["scrip_code"]) == {"NSE_MAIN"}
    geometry_ids = {g.id for g in mod.DEFAULT_AEM_V2_CONTRACT.geometries}
    assert set(frame["geometry_id"]).issubset(geometry_ids)
    assert set(frame["label"]).issubset({0, 1})
    registered_names = {f.name for f in mod.DEFAULT_AEM_V2_CONTRACT.features}
    assert registered_names.issubset(set(frame.columns))
    assert frame["net_r"].apply(np.isfinite).all()


def test_build_development_dataset_records_exclusions_when_peers_are_insufficient(
    monkeypatch, tmp_path
):
    daily, minute, symbols, included = _build_environment(include_all_peers=False)
    _patch_common(monkeypatch, daily, minute, symbols, included)

    frame, excluded, metadata = mod.build_development_dataset(
        root=tmp_path, output=tmp_path / "lab"
    )

    assert len(frame) == 0
    assert metadata["opportunities_found"] >= 1
    reasons = [e["reason"] for e in excluded]
    assert any("insufficient_peer_universe" in r for r in reasons)


def test_build_development_dataset_rejects_stale_source(monkeypatch, tmp_path):
    daily, minute, symbols, included = _build_environment()
    _patch_common(monkeypatch, daily, minute, symbols, included, source_sha="changed")

    with pytest.raises(ValueError, match="dataset_source_changed_since_universe_audit"):
        mod.build_development_dataset(root=tmp_path, output=tmp_path / "lab")


def test_freeze_development_dataset_writes_artifacts(monkeypatch, tmp_path):
    daily, minute, symbols, included = _build_environment()
    _patch_common(monkeypatch, daily, minute, symbols, included)

    report = mod.freeze_development_dataset(root=tmp_path, output=tmp_path / "lab")

    assert report["status"] == "development_dataset_frozen_not_evaluated"
    assert report["milestone"] == 4
    assert report["decision"]["change_live_behavior"] is False
    assert report["decision"]["change_canonical_baseline"] is False
    run_dir = tmp_path / f"lab/aem_v2/development_dataset/runs/{report['id']}"
    assert (run_dir / "events.csv").is_file()
    assert (run_dir / "excluded_events.json").is_file()
    events = pd.read_csv(run_dir / "events.csv")
    assert len(events) == report["resolved_rows"]
    latest = json.loads((tmp_path / "lab/aem_v2/development_dataset/latest.json").read_text())
    assert latest["id"] == report["id"]
