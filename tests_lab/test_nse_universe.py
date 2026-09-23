from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.nse_universe import run_nse_screen


def _database(tmp_path):
    path = tmp_path / "data/tradedesk.duckdb"
    path.parent.mkdir()
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE instruments(scrip_code VARCHAR, exch VARCHAR, kind VARCHAR, "
        "trading_symbol VARCHAR, series VARCHAR, instrument_name VARCHAR)"
    )
    con.execute(
        "CREATE TABLE candles(scrip_code VARCHAR, interval VARCHAR, ts BIGINT, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)"
    )
    return path, con


def _instrument(con, code, symbol, *, exch="NSE", kind="equity", series="EQ", name="EQUITY"):
    con.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?)", [code, exch, kind, symbol, series, name]
    )


def _daily(con, code, *, remove=None, last_offset=0, volume=1_000_000):
    dates = pd.bdate_range("2025-01-01", periods=120, tz="Asia/Kolkata")
    close = np.linspace(80, 100, len(dates))
    close[-10:] = np.linspace(98.5, 100, 10)
    rows = [
        (code, "1day", int(stamp.timestamp()), price - 0.2, price + 1.5, price - 1, price, volume)
        for index, (stamp, price) in enumerate(zip(dates, close, strict=True))
        if index != remove and index < len(dates) - last_offset
    ]
    con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)
    return dates


def _screen(tmp_path, on, **kwargs):
    return run_nse_screen(
        tmp_path,
        tmp_path / "outputs",
        as_of=on,
        now=datetime(2025, 12, 1, 18),
        **kwargs,
    )


def test_whole_nse_candidates_do_not_require_intraday_or_example_names(tmp_path):
    path, con = _database(tmp_path)
    _instrument(con, "NSE_11", "UNSEENONE")
    _instrument(con, "NSE_12", "UNSEENTWO")
    dates = _daily(con, "NSE_11")
    _daily(con, "NSE_12", volume=2_000_000)
    for code, exch, series, name in (
        ("BSE_11", "BSE", "EQ", "EQUITY"),
        ("NSE_13", "NSE", "BE", "EQUITY"),
        ("NSE_14", "NSE", "EQ", "FUTSTK"),
    ):
        _instrument(con, code, code, exch=exch, series=series, name=name)
        _daily(con, code)
    con.close()
    original = path.read_bytes()
    report = _screen(tmp_path, dates[-1].date(), shortlist_size=1, backfill_sessions=2)
    assert path.read_bytes() == original
    assert report["summary"]["daily_candidates"] == 2
    assert [row["symbol"] for row in report["candidates"]] == ["UNSEENTWO", "UNSEENONE"]
    assert len(report["shortlist"]) == 1
    assert report["candidates"][0]["success_probability"] is None
    assert report["coverage"][0]["intervals"][0]["stale_as_of"]
    assert report["backfill_plan"]["planned_missing_window_requests"] == 2
    reasons = report["summary"]["rejection_reasons"]
    assert reasons == {"not_nse": 1, "not_eq_series": 1, "not_cash_equity": 1}
    assert Path(report["artifact_path"]).exists()
    assert report["eligible_for_live"] is False


def test_future_candles_cannot_change_historical_screen_or_coverage(tmp_path):
    path, con = _database(tmp_path)
    _instrument(con, "NSE_11", "UNSEENONE")
    dates = _daily(con, "NSE_11")
    con.close()
    first = _screen(tmp_path, dates[-1].date(), backfill_sessions=2)
    with duckdb.connect(str(path)) as con:
        future_ts = int((dates[-1] + pd.Timedelta(days=1)).timestamp())
        for interval in ("1day", "1minute", "5minute"):
            con.execute(
                "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
                ["NSE_11", interval, future_ts, 200, 900, 100, 800, 1_000_000],
            )
    second = _screen(tmp_path, dates[-1].date(), backfill_sessions=2)
    assert first["screen_sha256"] == second["screen_sha256"]
    assert first["candidates"] == second["candidates"]
    assert first["coverage"] == second["coverage"]
    assert first["artifact_path"] != second["artifact_path"]


def test_missing_recent_session_stale_close_and_future_listing_are_rejected(tmp_path):
    _, con = _database(tmp_path)
    for code in ("NSE_11", "NSE_12", "NSE_13", "NSE_14"):
        _instrument(con, code, code)
    dates = _daily(con, "NSE_11")
    _daily(con, "NSE_12", remove=115)
    _daily(con, "NSE_13", last_offset=1)
    con.execute(
        "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
        [
            "NSE_14",
            "1day",
            int((dates[-1] + pd.Timedelta(days=1)).timestamp()),
            99,
            101,
            98,
            100,
            1_000_000,
        ],
    )
    con.close()
    report = _screen(tmp_path, dates[-1].date(), backfill_sessions=2)
    audit = {row["scrip_code"]: row for row in report["instrument_audit"]}
    assert audit["NSE_12"]["reasons"] == ["incomplete_recent_daily_sessions"]
    assert audit["NSE_13"]["reasons"] == ["stale_daily_close"]
    assert audit["NSE_14"]["reasons"] == ["no_daily_history_as_of"]
    assert [row["scrip_code"] for row in report["candidates"]] == ["NSE_11"]


def test_coverage_detects_partial_session_and_bounds_backfill_windows(tmp_path):
    _, con = _database(tmp_path)
    _instrument(con, "NSE_11", "UNSEENONE")
    dates = _daily(con, "NSE_11")
    for stamp in dates[-2:]:
        start = stamp + pd.Timedelta(hours=9, minutes=15)
        for minutes, total in ((1, 375), (5, 75)):
            rows = [
                (
                    "NSE_11",
                    f"{minutes}minute",
                    int((start + pd.Timedelta(minutes=i * minutes)).timestamp()),
                    99,
                    101,
                    98,
                    100,
                    1000,
                )
                for i in range(total)
                if not (stamp == dates[-1] and minutes == 1 and i == 10)
            ]
            con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)
    con.close()
    report = _screen(tmp_path, dates[-1].date(), backfill_sessions=2)
    m1, m5 = report["coverage"][0]["intervals"]
    assert m1["complete_sessions"] == 1
    assert m1["latest_session_complete"] is False
    assert m1["stale_as_of"] is False  # Current timestamp alone does not imply completeness.
    assert m1["planned_missing_window_requests"] == 1
    assert m5["complete_sessions"] == 2
    assert m5["planned_missing_window_requests"] == 0
    assert report["backfill_plan"]["planned_missing_window_requests"] == 1
    assert report["backfill_plan"]["full_span_request_upper_bound_before_retries"] == 2
    for window in m1["request_windows"]:
        assert (
            datetime.fromisoformat(window["end_exclusive"])
            - datetime.fromisoformat(window["start"])
        ) <= timedelta(days=7)


def test_today_before_close_is_rejected_and_missing_source_has_durable_report(tmp_path):
    with pytest.raises(ValueError, match="completed daily close"):
        run_nse_screen(
            tmp_path, tmp_path / "outputs", as_of="2025-06-18", now=datetime(2025, 6, 18, 10)
        )
    report = _screen(tmp_path, "2025-06-18")
    assert report["status"] == "blocked_source_unavailable"
    assert Path(report["artifact_path"]).exists()
    assert not (tmp_path / "data/tradedesk.duckdb").exists()
