from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

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


def test_multiday_calls_list_every_name_with_buy_day_distances_and_exit_date(
    tmp_path: Path,
) -> None:
    rows = [_call("AAA", "rsi2_dip_ema50"), _call("BBB", "random_eligible")]
    _write_jsonl(tmp_path / "data/reports/research_calls.jsonl", rows)
    messages = digests.multiday_calls("nse", root=tmp_path, today=date(2026, 10, 16))
    title, body = messages[0]
    assert title == "NSE multi-day calls"
    # armed Fri 16 Oct -> buy Mon 19 Oct open; risk = 3 x ATR 5 = 15, target 2R = 30;
    # exit = 10 sessions after the buy day (Mon 19 Oct) = Mon 02 Nov. Control not listed.
    assert "Buy at the Mon 19 Oct open" in body
    assert (
        "AAA [rsi2_dip_ema50] last close 100.00 | stop open-15.00 | target open+30.00 | "
        "exit by Mon 02 Nov close"
    ) in body
    assert "BBB" not in body and "RESEARCH ONLY" in body


def test_long_lists_split_across_messages_without_dropping_names(tmp_path: Path) -> None:
    rows = [_call(f"S{i:02d}", "rsi2_dip_ema50") for i in range(40)]
    _write_jsonl(tmp_path / "data/reports/research_calls.jsonl", rows)
    messages = digests.multiday_calls("nse", root=tmp_path, today=date(2026, 10, 16))
    text = "\n".join(body for _, body in messages)
    assert len(messages) == 3 and messages[0][0].endswith("(1/3)")
    assert all(f"S{i:02d} [" in text for i in range(40))
    assert text.count("RESEARCH ONLY") == 1


def test_calls_entered_today_use_the_actual_open(tmp_path: Path, monkeypatch) -> None:
    called = _call("CCC", "rsi2_dip_ema50", scrip_code="NSE_1", armed_on="2026-10-16")
    _write_jsonl(tmp_path / "data/reports/research_calls.jsonl", [called])
    monkeypatch.setattr(digests, "daily_opens", lambda *a, **k: {"NSE_1": 101.5})
    messages = digests.multiday_calls("nse", root=tmp_path, today=date(2026, 10, 19))
    entered = [body for title, body in messages if "entered today" in title]
    assert (
        entered
        and (
            "CCC [rsi2_dip_ema50] entry 101.50 | stop 86.50 | target 131.50 | "
            "exit by Mon 02 Nov close"
        )
        in entered[0]
    )


def test_crypto_counts_every_day_and_equity_skips_weekends() -> None:
    assert digests.add_sessions(date(2026, 10, 16), 1, "nse") == date(2026, 10, 19)
    assert digests.add_sessions(date(2026, 10, 16), 1, "crypto") == date(2026, 10, 17)


def test_rupees_per_10000_use_recorded_prices_and_the_round_trip() -> None:
    call = {"entry": 100.0, "exit_price": 110.0}
    assert round(digests.net_inr(call, 0.004), 2) == 960.0  # 10% gain less 0.4% round trip
    assert digests.net_inr({"entry": 100.0}, 0.004) is None


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
    assert digests.multiday_calls("crypto", root=tmp_path) == []
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


def test_the_dashboard_link_is_sent_once_per_change(tmp_path: Path) -> None:
    log = tmp_path / "data/logs/cloudflare_tunnel.log"
    log.parent.mkdir(parents=True)
    log.write_text("x https://old-one.trycloudflare.com\ny https://new-two.trycloudflare.com\n")
    title, body = digests.dashboard_link(root=tmp_path) or ("", "")
    assert "https://new-two.trycloudflare.com" in body
    assert digests.dashboard_link(root=tmp_path) is None  # unchanged: nothing to send
    log.write_text("")  # rotated log: the last link is remembered, nothing re-sent
    assert digests.dashboard_link(root=tmp_path) is None


def test_each_waiting_review_item_is_announced_with_where_to_act(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "data" / "reviews" / "queue.jsonl",
        [
            {"id": "nse:1", "market": "nse", "status": "pending",
             "title": "NEW DETECTOR r_x on nse", "detail": "nr7_breakout below 30%"},
            {"id": "nse:0", "market": "nse", "status": "approved", "title": "old", "detail": ""},
        ],
    )  # fmt: skip
    items = digests.review_items(root=tmp_path)
    assert [key for key, _, _ in items] == ["review:nse:1"]
    _, title, body = items[0]
    assert title == "Review waiting (NSE): NEW DETECTOR r_x on nse" and "Review tab" in body


def test_potential_movers_list_their_own_track_record(tmp_path: Path) -> None:
    report = {
        "status": "available", "data_stale": False, "as_of": "2026-10-19",
        "candidates": [
            {"symbol": f"S{i}", "tier": "capitulation", "close": 350.45,
             "five_session_change_pct": -10.0, "model_probability": 0.2}
            for i in range(12)
        ],
        "forward_tracking": {"picks": 50, "up5_rate": 0.12, "down5_rate": 0.16,
                             "same_day_universe_up5_rate": 0.03},
    }  # fmt: skip
    path = tmp_path / "data" / "m14_m18" / "potential_movers" / "nse" / "latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding="utf-8")

    key, title, body = digests.potential_movers("nse", root=tmp_path)
    assert key == "movers:nse:2026-10-19" and "2026-10-19" in title
    assert "S0  capitulation  close 350.45  5d -10.0%  p(up5%) 20%" in body
    assert "12% of 50 past picks rose 5%+" in body and "+2 more" in body
    assert digests.RESEARCH in body
    report["data_stale"] = True
    path.write_text(json.dumps(report), encoding="utf-8")
    assert digests.potential_movers("nse", root=tmp_path) is None


def test_every_call_push_carries_its_rules_live_record_against_random(tmp_path: Path) -> None:
    done = {"entry": 100.0, "outcome": "timeout", "armed_on": "2026-10-01"}
    rows = [
        _call("W1", "dip", **done, exit_price=90.0),
        _call("W2", "dip", **done, exit_price=120.0),
        _call("R1", "random_eligible", **done, exit_price=110.0),
        _call("NEW", "dip", armed_on="2026-10-20"),
    ]
    _write_jsonl(tmp_path / "data/reports/research_calls_crypto.jsonl", rows)
    body = digests.multiday_calls("crypto", root=tmp_path, today=date(2026, 10, 20))[0][1]
    cost = digests.FALLBACK_ROUND_TRIP["crypto"]  # TDS included: it is cut at the sale
    # After every charge cut at the trade; no year-end income tax is taken off.
    dip = (10_000 * (-0.10 - cost) + 10_000 * (0.20 - cost)) / 2
    rnd = 10_000 * (0.10 - cost)
    assert "after all charges incl. TDS" in body and "tax" not in body.split("RESEARCH")[0]
    assert f"dip: 2 calls, avg {digests._rupees(dip)}, 50% made money" in body
    assert "worse than random picks" in body
    assert f"random picks: 1 calls, avg {digests._rupees(rnd)}, 100% made money" in body


def test_equity_figures_count_every_charge_including_stt() -> None:
    # Rs 10,000 delivery round trip: Rs 56.41 in all, Rs 20 of it STT (0.1% each side).
    root = Path(__file__).resolve().parents[2]
    assert digests.round_trip("nse", root=root) == pytest.approx(0.005641, abs=0.0002)


def test_the_evening_summary_is_one_push_with_the_top_calls_and_where_the_rest_are(
    tmp_path: Path,
) -> None:
    rows = [_call(f"S{i:02d}", "dip", armed_on="2026-10-23") for i in range(12)]
    rows.append(_call("R1", "random_eligible", armed_on="2026-10-23"))
    _write_jsonl(tmp_path / "data/reports/research_calls.jsonl", rows)
    (tmp_path / "data/reports/dashboard_link.json").write_text(
        json.dumps({"url": "https://example.trycloudflare.com"}), encoding="utf-8"
    )
    out = digests.evening_summary("nse", date(2026, 10, 23), root=tmp_path)
    assert len(out) == 1
    title, body = out[0]
    assert title == "NSE evening summary 23 Oct"
    assert "NSE end of day 2026-10-23" in body and "intraday study" in body
    assert "Multi-day calls armed 2026-10-23: 12 (dip 12), buy at the Mon 26 Oct open" in body
    assert body.count("[dip] stop open-") == digests.SUMMARY_TOP
    assert "+7 more on the dashboard: Research tab (nse)" in body
    assert "R1" not in body  # the random control is never listed as a call
    assert "Dashboard: https://example.trycloudflare.com" in body
    assert body.count("RESEARCH ONLY") == 1 and len(body) <= digests.MAX_MESSAGE_CHARS + 200


def test_a_failed_host_job_marks_only_its_own_section(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "data/reports/research_calls_crypto.jsonl", [])
    _, body = digests.evening_summary(
        "crypto", date(2026, 10, 23), root=tmp_path, multiday_ok=False
    )[0]
    assert "CRYPTO end of day" in body and "today's job failed" in body


def test_long_summaries_split_on_whole_sections() -> None:
    big = [[f"line {i} " + "x" * 90 for i in range(25)] for _ in range(3)]
    parts = digests._split("T", big, ["END"])
    assert len(parts) > 1 and parts[0][0] == f"T (1/{len(parts)})"
    assert all(len(body) <= digests.MAX_MESSAGE_CHARS for _, body in parts)
    assert parts[-1][1].endswith("END") and "END" not in parts[0][1]
