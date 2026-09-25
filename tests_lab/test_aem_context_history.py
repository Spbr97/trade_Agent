import csv
import json

import pytest
from tradedesk_lab.aem_context_history import (
    CONTEXT_SYMBOLS,
    build_context_plan,
    collect_context_history,
)


def _source(tmp_path):
    root, output = tmp_path / "root", tmp_path / "output"
    dataset_id = "a" * 32
    folder = output / "aem_staged/datasets" / dataset_id
    folder.mkdir(parents=True)
    dates = [
        day.date().isoformat()
        for day in __import__("pandas").bdate_range("2026-01-01", periods=120)
    ]
    manifest = {
        "id": dataset_id,
        "source": {
            "benchmark_code": "NSE_40000001",
            "evaluation_dates": dates,
        },
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    instruments = root / "data/instruments/index.csv"
    instruments.parent.mkdir(parents=True)
    with instruments.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["EXCH", "SEGMENT", "SECURITY_ID"])
        writer.writerows(
            [
                ["NSE", "NIFTY 50", "40000001"],
                ["NSE", "BANK NIFTY", "40000003"],
                ["NSE", "Nifty Financial", "40000100"],
            ]
        )
    return root, output, dataset_id


def test_context_plan_freezes_three_indices_and_120_sessions(tmp_path):
    root, output, dataset_id = _source(tmp_path)
    plan = build_context_plan(root, output, dataset_id)
    assert tuple(plan["symbols"]) == CONTEXT_SYMBOLS
    assert plan["symbols"]["NIFTY 50"] == "NSE_40000001"
    assert len(plan["sessions"]) == 120
    assert plan["expected_rows"] == 3 * 120 * 375
    assert all(1 <= len(window["sessions"]) <= 5 for window in plan["windows"])
    assert plan["sha256"]


def test_context_plan_fails_closed_on_wrong_benchmark_or_calendar(tmp_path):
    root, output, dataset_id = _source(tmp_path)
    path = output / "aem_staged/datasets" / dataset_id / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["source"]["benchmark_code"] = "NSE_9"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="benchmark differs"):
        build_context_plan(root, output, dataset_id)

    manifest["source"]["benchmark_code"] = "NSE_40000001"
    manifest["source"]["evaluation_dates"].pop()
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="120-session"):
        build_context_plan(root, output, dataset_id)


def test_context_collection_stops_before_client_when_preflight_blocks(tmp_path):
    root, output, dataset_id = _source(tmp_path)

    def forbidden_client(**_kwargs):
        raise AssertionError("blocked preflight must not construct a broker client")

    report = collect_context_history(
        root,
        output,
        dataset_id=dataset_id,
        max_requests=1,
        client_factory=forbidden_client,
        preflight=lambda: {
            "allowed": False,
            "reasons": ["protected_weekday_market_window"],
        },
    )

    assert report["status"] == "blocked_preflight"
    assert report["requests_this_run"] == 0
    assert report["requests_recorded_all_runs"] == 0
    assert report["context_ready"] is False
    assert report["coverage"]["missing_sessions"] == 360
