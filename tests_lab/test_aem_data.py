from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_data import read_aem_source, regular_minutes
from tradedesk_lab.aem_dataset import _complete_signal_session, build_events
from tradedesk_lab.mcb_features import time_of_day_rvol


def _bars(index):
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100},
        index=index,
    )


def _source_store(tmp_path):
    days = pd.bdate_range("2026-01-01", periods=32, tz="Asia/Kolkata")
    (tmp_path / "data").mkdir()
    with duckdb.connect(str(tmp_path / "data/tradedesk.duckdb")) as con:
        con.execute(
            "CREATE TABLE instruments(scrip_code VARCHAR, trading_symbol VARCHAR, "
            "exch VARCHAR, kind VARCHAR, series VARCHAR)"
        )
        con.execute(
            "INSERT INTO instruments VALUES "
            "('NSE_INDEX','NIFTY 50','NSE','index',''),"
            "('NSE_NEW','UNSEEN','NSE','equity','EQ')"
        )
        rows = []
        for number, day in enumerate(days):
            for code in ("NSE_INDEX", "NSE_NEW"):
                rows.append([code, "1day", int(day.timestamp()), 100, 101, 99, 100.5, 1000])
            rows.append(
                [
                    "NSE_NEW",
                    "1minute",
                    int((day + pd.Timedelta(hours=9, minutes=15)).timestamp()),
                    100,
                    101,
                    99,
                    100.5,
                    100 + number,
                ]
            )
        raw = pd.DataFrame(
            rows, columns=["scrip_code", "interval", "ts", "open", "high", "low", "close", "volume"]
        )
        con.register("raw", raw)
        con.execute("CREATE TABLE candles AS SELECT * FROM raw")
    return days


def _read_args(days, sessions=5):
    return dict(
        contract=AemContract(),
        sessions=sessions,
        benchmark_symbol="NIFTY 50",
        as_of=days[-2].date(),
        now=datetime(2026, 3, 1, tzinfo=ZoneInfo("Asia/Kolkata")),
    )


def test_reader_uses_d1_m1_and_keeps_rvol_warmup(tmp_path):
    days = _source_store(tmp_path)
    as_of = days[-2].date()
    args = _read_args(days)
    daily, minute, symbols, calendar, evaluated, source = read_aem_source(tmp_path, **args)
    assert symbols == {"NSE_NEW": "UNSEEN"}  # no M5 data exists
    assert len(minute["NSE_NEW"]) == 31  # full observed history through as_of
    assert len(evaluated) == 5 and max(calendar) == as_of
    rvol = time_of_day_rvol(minute["NSE_NEW"])
    full = _bars(pd.DatetimeIndex(days[:-1] + pd.Timedelta(hours=9, minutes=15)))
    full["volume"] = np.arange(100, 131)
    np.testing.assert_array_equal(rvol.iloc[-5:].index.asi8, full.iloc[-5:].index.asi8)
    np.testing.assert_allclose(rvol.iloc[-5:].to_numpy(), time_of_day_rvol(full).iloc[-5:])
    assert max(daily["NSE_NEW"].index.date) == as_of
    consumed = source["symbols"]["NSE_NEW"]["minute"]
    assert consumed["rows"] == 31
    assert consumed["from"] == str(minute["NSE_NEW"].index[0])
    assert consumed["through"] == str(minute["NSE_NEW"].index[-1])
    # Future candle edits cannot change this historical source fingerprint.
    with duckdb.connect(str(tmp_path / "data/tradedesk.duckdb")) as con:
        con.execute("UPDATE candles SET volume=999999 WHERE ts>=?", [int(days[-1].timestamp())])
    assert read_aem_source(tmp_path, **args)[-1]["sha256"] == source["sha256"]


def test_sparse_slot_rvol_does_not_depend_on_evaluation_window(tmp_path):
    days = _source_store(tmp_path)
    missing_slot = days[10] + pd.Timedelta(hours=9, minutes=15)
    with duckdb.connect(str(tmp_path / "data/tradedesk.duckdb")) as con:
        # Retain the session with a different minute, but remove this slot's
        # observation: counting prior observed sessions alone is insufficient.
        con.execute(
            "UPDATE candles SET ts=ts+60 WHERE scrip_code='NSE_NEW' "
            "AND interval='1minute' AND ts=?",
            [int(missing_slot.timestamp())],
        )
    short = read_aem_source(tmp_path, **_read_args(days, sessions=5))
    long = read_aem_source(tmp_path, **_read_args(days, sessions=10))
    full = _bars(pd.DatetimeIndex(days[:-1] + pd.Timedelta(hours=9, minutes=15)))
    full["volume"] = np.arange(100, 131)
    full = full.drop(missing_slot)
    evaluated_slots = full.index[full.index.date >= short[4][0]]
    expected = time_of_day_rvol(full).loc[evaluated_slots]
    for result in (short, long):
        actual = time_of_day_rvol(result[1]["NSE_NEW"]).loc[evaluated_slots]
        np.testing.assert_array_equal(actual.index.asi8, expected.index.asi8)
        np.testing.assert_allclose(actual, expected)
    assert short[-1]["symbols"] == long[-1]["symbols"]


def test_source_hash_covers_benchmark_session_before_evaluation_window(tmp_path):
    days = _source_store(tmp_path)
    args = _read_args(days)
    before = read_aem_source(tmp_path, **args)[-1]
    prior_day = days[-7]  # directly before the five dates ending at days[-2]
    with duckdb.connect(str(tmp_path / "data/tradedesk.duckdb")) as con:
        con.execute(
            "DELETE FROM candles WHERE scrip_code='NSE_INDEX' AND interval='1day' AND ts=?",
            [int(prior_day.timestamp())],
        )
    after = read_aem_source(tmp_path, **args)[-1]
    assert before["symbols"] == after["symbols"]
    assert before["evaluation_dates"] == after["evaluation_dates"]
    assert str(prior_day.date()) in before["benchmark_calendar"]
    assert str(prior_day.date()) not in after["benchmark_calendar"]
    assert before["benchmark_calendar_sha256"] != after["benchmark_calendar_sha256"]
    assert before["sha256"] != after["sha256"]


def test_regular_session_requires_m1_and_excludes_endpoint():
    minutes = pd.date_range("2026-09-18 09:15", periods=376, freq="min", tz="Asia/Kolkata")
    regular = regular_minutes(_bars(minutes))
    assert len(regular) == 375 and _complete_signal_session(regular)
    assert not _complete_signal_session(regular.drop(regular.index[100]))
    assert not _complete_signal_session(regular.iloc[::5])


def test_stale_daily_snapshot_is_not_a_candidate():
    days = pd.bdate_range("2026-08-01", periods=10, tz="Asia/Kolkata")
    session = _bars(
        pd.date_range(f"{days[-1].date()} 09:15", periods=375, freq="min", tz="Asia/Kolkata")
    )
    # Daily candles stop two sessions before the current one.
    events, audit = build_events(
        {"NSE_NEW": _bars(days[:-2])},
        {"NSE_NEW": session},
        {"NSE_NEW": "UNSEEN"},
        risk=None,
        costs=None,
        trading_dates=list(days.date),
        evaluation_dates=[days[-1].date()],
    )
    assert events.empty
    assert audit["stale_or_missing_prior_daily_close"] == 1
