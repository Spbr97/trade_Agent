from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("send_digest", ROOT / "scripts" / "send_digest.py")
assert _spec and _spec.loader
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)


def _health(tmp_path: Path, started: str, stages: dict[str, str]) -> Path:
    rows = [{"name": k, "status": v} for k, v in stages.items()]
    report = {"started_at": started, "stages": rows}
    (tmp_path / "nse.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def test_drift_goes_out_when_the_prices_loaded_though_a_later_stage_failed(
    tmp_path: Path,
) -> None:
    # 2026-10-19 exactly: data in, the research-only M8 stage failed, the rest skipped.
    health = _health(
        tmp_path,
        "2026-10-19T10:30:00+00:00",
        {
            "data_load": "completed",
            "benchmark_load": "completed",
            "scan": "completed",
            "signal_and_m8_m11": "failed",
            "research_tracker": "skipped_dependency_failure",
        },
    )
    assert sd.data_ready("nse", date(2026, 10, 19), health=health)


def test_drift_holds_when_prices_did_not_load_or_the_run_is_old(tmp_path: Path) -> None:
    failed = _health(
        tmp_path, "2026-10-19T10:30:00+00:00", {"data_load": "failed", "benchmark_load": "x"}
    )
    assert not sd.data_ready("nse", date(2026, 10, 19), health=failed)
    old = _health(
        tmp_path,
        "2026-10-16T10:30:00+00:00",
        {"data_load": "completed", "benchmark_load": "completed"},
    )
    assert not sd.data_ready("nse", date(2026, 10, 19), health=old)
    assert not sd.data_ready("nse", date(2026, 10, 19), health=tmp_path / "missing")


def test_review_items_go_out_as_one_message_each_announced_once() -> None:
    items = [
        ("review:a", "Review waiting (NSE): NEW DETECTOR x on nse", "nr7 fails\nApprove..."),
        ("review:b", "Review waiting (NSE): NEW DETECTOR y on nse", "nr7 fails\nApprove..."),
        ("review:c", "Review waiting (BSE): NEW DETECTOR z on bse", "base fails\nApprove..."),
    ]
    keys, title, body = sd.review_batch(items, {"review:c": "2026-10-19T17:00:00+05:30"})
    assert keys == ["review:a", "review:b"] and title == "2 reviews waiting"
    assert "- (NSE): NEW DETECTOR x on nse" in body and body.count("nr7 fails") == 1
    assert sd.review_batch(items[:1], {}) == ([items[0][0]], items[0][1], items[0][2])
    assert sd.review_batch(items, dict.fromkeys(["review:a", "review:b", "review:c"], "t")) is None


def test_the_sent_record_keeps_the_newest_keys_not_the_last_names() -> None:
    seen = {f"review:{i:03d}": f"2026-10-01T00:00:{i % 60:02d}" for i in range(5)}
    seen["close:today"] = "2026-10-20T16:08:00"
    kept = dict(sd.newest(seen, 3))
    assert "close:today" in kept and len(kept) == 3
