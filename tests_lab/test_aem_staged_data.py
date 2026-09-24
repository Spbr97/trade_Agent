import json
from datetime import datetime, time, timedelta

import duckdb
import pytest
from tests_lab.test_aem_universe_plan import IDENTIFIER, _setup, _stock
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT
from tradedesk_lab.aem_history import collect_history
from tradedesk_lab.aem_staged_data import read_staged_aem_source
from tradedesk_lab.aem_universe_plan import freeze_universe_plan

from tradedesk.broker.indstocks.models import IST


def _ready(tmp_path, *, opening_gap=False):
    output, database, con, dates = _setup(tmp_path)
    _stock(con, dates, "NSE_1")
    con.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?)",
        ["NSE_INDEX", "NSE", "index", "NIFTY 50", "", "INDEX"],
    )
    con.executemany(
        "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
        [
            ("NSE_INDEX", "1day", int(day.timestamp()), 100, 101, 99, 100, 1_000_000)
            for day in dates
        ],
    )
    con.close()
    plan = freeze_universe_plan(tmp_path, output, dataset_id=IDENTIFIER, shortlist_size=1)
    sessions = [
        *map(lambda value: datetime.fromisoformat(value).date(), plan["warmup_dates"]),
        *map(lambda value: datetime.fromisoformat(value).date(), plan["evaluation_dates"]),
    ]
    gap_day = sessions[-2]
    rows = []
    for day in sessions:
        start = datetime.combine(day, time(9, 15), IST)
        first = 21 if opening_gap and day == gap_day else 0
        rows.extend(
            (
                "NSE_1",
                "1minute",
                int((start + timedelta(minutes=i)).timestamp()),
                100,
                101,
                99,
                100,
                1000,
            )
            for i in range(first, 375)
        )
    with duckdb.connect(str(database)) as con:
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)
    report = collect_history(tmp_path, output, plan_id=plan["id"], max_requests=0)
    assert report["requests_this_run"] == 0
    return output, database, plan, sessions, gap_day


def _read(tmp_path, output, plan):
    return read_staged_aem_source(
        tmp_path,
        output,
        plan_id=plan["id"],
        contract=DEFAULT_AEM_CONTRACT,
        benchmark_symbol="NIFTY 50",
    )


def test_staged_reader_joins_frozen_m1_to_read_only_daily_and_benchmark(tmp_path):
    output, database, plan, sessions, _ = _ready(tmp_path)
    before = database.read_bytes()
    daily, minute, symbols, calendar, evaluation, source = _read(tmp_path, output, plan)
    assert database.read_bytes() == before
    assert symbols == {"NSE_1": "NSE_1"}
    assert len(daily["NSE_1"]) == 65
    assert len(minute["NSE_1"]) == len(sessions) * 375
    assert evaluation == [datetime.fromisoformat(day).date() for day in plan["evaluation_dates"]]
    assert [day for day in calendar if sessions[0] <= day <= sessions[-1]] == sessions
    assert source["source_version"] == "aem-staged-preperiod-m1-v1"
    assert source["plan_id"] == plan["id"]
    assert source["coverage"]["NSE_1"]["missing_or_incomplete_sessions"] == []
    assert source["historical_constituents_available"] is False
    json.dumps(source, allow_nan=False)


def test_incomplete_provider_session_is_retained_and_audited(tmp_path):
    output, _, plan, sessions, gap_day = _ready(tmp_path, opening_gap=True)
    _, minute, _, _, _, source = _read(tmp_path, output, plan)
    assert len(minute["NSE_1"]) == len(sessions) * 375 - 21
    audit = source["coverage"]["NSE_1"]
    assert audit["complete_sessions"] == len(sessions) - 1
    assert audit["missing_or_incomplete_sessions"] == [str(gap_day)]


def test_reader_fails_closed_on_pending_collection_or_metadata_drift(tmp_path):
    output, database, plan, _, _ = _ready(tmp_path)
    state_path = output / "aem_history" / plan["id"] / "state.json"
    state = json.loads(state_path.read_text())
    state["attempts"].append({"status": "pending"})
    state_path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="pending"):
        _read(tmp_path, output, plan)
    state["attempts"].pop()
    state_path.write_text(json.dumps(state))
    with duckdb.connect(str(database)) as con:
        con.execute("UPDATE instruments SET trading_symbol='CHANGED' WHERE scrip_code='NSE_1'")
    with pytest.raises(ValueError, match="metadata changed"):
        _read(tmp_path, output, plan)


def test_reader_rejects_wrong_contract(tmp_path):
    output, _, plan, _, _ = _ready(tmp_path)
    from tradedesk_lab.aem_contract import AemContract

    with pytest.raises(ValueError, match="contract differ"):
        read_staged_aem_source(
            tmp_path,
            output,
            plan_id=plan["id"],
            contract=AemContract(target_pct=0.007),
            benchmark_symbol="NIFTY 50",
        )
