from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tradedesk import digests

DAY = date(2026, 10, 16)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _call(symbol: str, rule: str, **extra) -> dict:
    base = {
        "rule": rule, "symbol": symbol, "armed_on": "2026-10-16", "close_at_arm": 100.0,
        "atr_at_arm": 5.0, "stop_atr": 3.0, "target_r": 2.0, "max_hold": 10,
        "entry": None, "stop": None, "t1": None, "outcome": None, "label": None,
        "r_multiple": None, "resolved_at": None,
    }  # fmt: skip
    return base | extra


def test_multiday_calls_give_names_reference_stop_target_and_exit(tmp_path: Path) -> None:
    rows = [_call("AAA", "rsi2_dip_ema50"), _call("BBB", "random_eligible")]
    _write_jsonl(tmp_path / "data/reports/research_calls.jsonl", rows)
    title, body = digests.multiday_calls("nse", root=tmp_path) or ("", "")
    assert "NSE multi-day calls" in title
    # risk = 3 ATR = 15 -> stop 85, target = 100 + 2 * 15 = 130; the control rule is not listed
    assert "AAA [rsi2_dip_ema50] ref 100.00 stop 85.00 target 130.00 exit by session 10" in body
    assert "BBB" not in body and "RESEARCH ONLY" in body


def test_an_entered_call_uses_its_own_recorded_levels(tmp_path: Path) -> None:
    entered = _call("CCC", "rsi2_dip_ema50", entry=101.5, stop=90.0, t1=124.5)
    _write_jsonl(tmp_path / "data/reports/research_calls.jsonl", [entered])
    _, body = digests.multiday_calls("nse", root=tmp_path) or ("", "")
    assert "CCC [rsi2_dip_ema50] ref 101.50 stop 90.00 target 124.50" in body


def test_eod_counts_only_what_resolved_that_day(tmp_path: Path) -> None:
    today = "2026-10-16T16:30:00+05:30"
    old = "2026-10-15T16:30:00+05:30"
    _write_jsonl(
        tmp_path / "data/reports/research_calls.jsonl",
        [
            _call("A", "x", outcome="stop", label=0, r_multiple=-1.0, resolved_at=today),
            _call("B", "x", outcome="target", label=1, r_multiple=2.0, resolved_at=today),
            _call("C", "x", outcome="stop", label=0, r_multiple=-1.0, resolved_at=old),
            _call("D", "x"),
        ],
    )
    _write_jsonl(
        tmp_path / "data/reports/nse_signal_tracking.jsonl",
        [{"outcome": None}, {"outcome": "stop", "r_multiple": -1.0, "resolved_at": today}],
    )
    _, body = digests.eod_summary("nse", DAY, root=tmp_path)
    assert "Tracked signals: resolved 1 (stop 1), mean -1.00R; open 1" in body
    assert "armed 4; resolved 2 (stop 1, target 1), mean +0.50R; open 1" in body


def test_close_job_reports_failures_and_the_scan_size(tmp_path: Path) -> None:
    folder = tmp_path / "data/watchlists"
    folder.mkdir(parents=True)
    (folder / "2026-10-16.json").write_text(
        json.dumps({"entries": [{}, {}, {}], "regime": {"regime": "risk_off"}}), encoding="utf-8"
    )
    title, body = digests.close_job(
        "nse", "tradedesk-after-close", "success", "0", today=DAY, root=tmp_path
    )
    assert title == "NSE close job finished OK" and "3 entries, regime risk_off" in body
    failed, text = digests.close_job(
        "bse", "tradedesk-bse-after-close", "exit-code", "1", today=DAY, root=tmp_path
    )
    assert failed == "BSE close job FAILED" and "exit 1" in text


def test_intraday_check_reads_the_nightly_report(tmp_path: Path) -> None:
    report = {
        "pick_record": {
            "consensus": {
                "latest_session": "2026-10-16",
                "latest": {"picks": 12, "winners": 1, "beat_market": 1},
                "all": {"picks": 24, "winners": 7, "beat_market_rate": 0.2083},
                "sessions": 2,
                "cutoff": {"status": "collecting", "picks": 24, "picks_required": 100,
                           "beat_rate_required": 0.5},
            }
        },
        "forward": [{"candidate": "consensus_top3", "forward_days": 2, "mean_net": -0.004,
                     "t_excess": -1.2}],
        "last_search": {"survivors": ["a", "b"]},
    }  # fmt: skip
    path = tmp_path / "data/m14_m18/intraday_mover_study/nse/latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    _, body = digests.intraday_check("nse", root=tmp_path)
    assert "12, winners 1, beat market 1" in body and "24 picks, winners 7" in body
    assert "Cut-off: collecting (24/100" in body and "Search survivors: 2" in body


def test_missing_data_never_breaks_a_digest(tmp_path: Path) -> None:
    assert digests.multiday_calls("crypto", root=tmp_path) is None
    assert "No intraday study report" in digests.intraday_check("bse", root=tmp_path)[1]
    assert "Failing tasks now: 0" in digests.weekly(DAY, root=tmp_path)[1]


def test_every_hooked_job_exists_in_the_schedule() -> None:
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("ru", root / "deploy/linux/render_units.py")
    assert spec and spec.loader
    ru = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ru)
    jobs = {
        j["name"] for j in json.loads((root / "deploy/schedule.json").read_text("utf-8"))["jobs"]
    }
    assert set(ru.DIGEST_HOOKS) <= jobs
    line = ru.digest_lines("tradedesk-after-close", root="/opt/tradedesk")[0]
    assert (
        line.startswith("ExecStopPost=-")
        and "$SERVICE_RESULT" in line
        and "close --market nse" in line
    )
