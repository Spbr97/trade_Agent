import json

import numpy as np
import pandas as pd
import tradedesk_lab.aem_v2_universe_audit as mod
from tradedesk_lab.aem_v2_universe_audit import _audit_one_code


def _daily_frame(n: int, *, close: float = 100.0, volume: float = 200_000.0) -> pd.DataFrame:
    idx = pd.bdate_range("2026-01-01", periods=n, tz="Asia/Kolkata")
    closes = np.full(n, close)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(n, volume),
        },
        index=idx,
    )


def test_clean_liquid_session_is_included():
    frame = _daily_frame(90)
    evaluation = [frame.index[-1].date()]
    results, hits = _audit_one_code("NSE_1", frame, evaluation=evaluation, missing_sessions=set())
    assert results[0].included is True
    assert results[0].reasons == ()
    assert hits == []


def test_missing_session_is_excluded():
    frame = _daily_frame(90)
    evaluation = [frame.index[-1].date()]
    session = str(evaluation[0])
    results, _ = _audit_one_code(
        "NSE_1", frame, evaluation=evaluation, missing_sessions={session}
    )
    assert results[0].included is False
    assert "incomplete_m1_session" in results[0].reasons


def test_insufficient_daily_warmup_is_excluded():
    frame = _daily_frame(90)
    evaluation = [frame.index[30].date()]  # only 30 prior rows, below the 60-session floor
    results, _ = _audit_one_code("NSE_1", frame, evaluation=evaluation, missing_sessions=set())
    assert "insufficient_daily_warmup" in results[0].reasons


def test_illiquid_session_is_excluded():
    frame = _daily_frame(90, close=10.0, volume=1_000.0)
    evaluation = [frame.index[-1].date()]
    results, _ = _audit_one_code("NSE_1", frame, evaluation=evaluation, missing_sessions=set())
    assert "inadequate_liquidity" in results[0].reasons


def test_suspected_corporate_action_session_is_excluded():
    frame = _daily_frame(90)
    bonus_idx = 70
    prior_close = float(frame["close"].iloc[bonus_idx - 1])
    frame.iloc[bonus_idx, frame.columns.get_loc("open")] = prior_close * 0.5
    frame.iloc[bonus_idx, frame.columns.get_loc("close")] = prior_close * 0.5
    evaluation = [frame.index[bonus_idx].date()]
    results, hits = _audit_one_code("NSE_1", frame, evaluation=evaluation, missing_sessions=set())
    assert "suspected_unadjusted_corporate_action" in results[0].reasons
    assert len(hits) == 1


def test_run_universe_audit_orchestrates_and_freezes_output(tmp_path, monkeypatch):
    frame_liquid = _daily_frame(90)
    frame_illiquid = _daily_frame(90, close=10.0, volume=500.0)
    evaluation = [frame_liquid.index[-1].date()]
    daily = {"NSE_1": frame_liquid, "NSE_2": frame_illiquid}
    source = {
        "coverage": {
            "NSE_1": {"missing_or_incomplete_sessions": []},
            "NSE_2": {"missing_or_incomplete_sessions": []},
        },
        "sha256": "abc",
    }

    class FakeContract:
        sha256 = "contract-hash"

    def fake_load_v1_manifest(output, dataset_id):
        manifest = {"plan_id": "plan-1", "source": {"sha256": "abc"}}
        return "dataset-1", tmp_path / "folder", manifest, FakeContract()

    def fake_read_staged_aem_source(root, output, *, plan_id, contract, benchmark_symbol):
        return daily, {}, {}, [], evaluation, source

    class FakeUniverse:
        benchmark = "NIFTY 50"

    class FakeSettings:
        universe = FakeUniverse()

    monkeypatch.setattr(mod, "_load_v1_manifest", fake_load_v1_manifest)
    monkeypatch.setattr(mod, "read_staged_aem_source", fake_read_staged_aem_source)
    monkeypatch.setattr(mod, "load_config", lambda root: FakeSettings())

    report = mod.run_universe_audit(root=tmp_path, output=tmp_path / "lab")

    assert report["universe_symbols"] == 2
    assert report["total_code_sessions"] == 2
    assert report["included_code_sessions"] == 1
    assert report["excluded_code_sessions"] == 1
    assert report["exclusion_reason_counts"] == {"inadequate_liquidity": 1}
    assert report["decision"]["change_live_behavior"] is False
    latest = json.loads((tmp_path / "lab/aem_v2/universe_audit/latest.json").read_text())
    assert latest["id"] == report["id"]
    written = json.loads(
        (tmp_path / f"lab/aem_v2/universe_audit/runs/{report['id']}/report.json").read_text()
    )
    assert written == report
    included_csv = pd.read_csv(
        tmp_path / f"lab/aem_v2/universe_audit/runs/{report['id']}/included_pairs.csv"
    )
    assert included_csv.to_dict("records") == [
        {"scrip_code": "NSE_1", "session": str(evaluation[0])}
    ]


def test_run_universe_audit_rejects_stale_source(tmp_path, monkeypatch):
    frame = _daily_frame(90)
    evaluation = [frame.index[-1].date()]
    daily = {"NSE_1": frame}
    source = {
        "coverage": {"NSE_1": {"missing_or_incomplete_sessions": []}},
        "sha256": "changed",
    }

    class FakeContract:
        sha256 = "contract-hash"

    def fake_load_v1_manifest(output, dataset_id):
        manifest = {"plan_id": "plan-1", "source": {"sha256": "original"}}
        return "dataset-1", tmp_path / "folder", manifest, FakeContract()

    def fake_read_staged_aem_source(root, output, *, plan_id, contract, benchmark_symbol):
        return daily, {}, {}, [], evaluation, source

    class FakeUniverse:
        benchmark = "NIFTY 50"

    class FakeSettings:
        universe = FakeUniverse()

    monkeypatch.setattr(mod, "_load_v1_manifest", fake_load_v1_manifest)
    monkeypatch.setattr(mod, "read_staged_aem_source", fake_read_staged_aem_source)
    monkeypatch.setattr(mod, "load_config", lambda root: FakeSettings())

    import pytest

    with pytest.raises(ValueError, match="dataset_source_changed"):
        mod.run_universe_audit(root=tmp_path, output=tmp_path / "lab")
