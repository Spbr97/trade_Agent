import asyncio
from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest
from tradedesk_lab.aem_staging import StageStore

from tradedesk.broker.indstocks.models import IST, Candle, Interval

DAY = date(2026, 3, 25)
OPEN = datetime(2026, 3, 25, 9, 15, tzinfo=IST)
CODE = "NSE_3045"


def bar(index=0, **updates):
    candle = Candle(
        scrip_code=CODE,
        interval=Interval.M1,
        ts=OPEN + timedelta(minutes=index),
        open=100,
        high=102,
        low=99,
        close=101,
        volume=10,
    )
    return candle.model_copy(update=updates)


@pytest.fixture
def store(tmp_path):
    with StageStore(tmp_path / "aem_history/stage.duckdb", output_root=tmp_path) as result:
        yield result


def ingest(store, bars, **updates):
    options = dict(
        codes=[CODE],
        start=DAY,
        end_exclusive=DAY + timedelta(days=1),
        sessions=[DAY],
        origin="test",
    )
    options.update(updates)
    return store.ingest({CODE: bars}, **options)


def test_full_session_exact_duplicates_resume_and_audit(tmp_path):
    path = tmp_path / "aem_history/stage.duckdb"
    candles = [bar(i) for i in range(375)]
    with StageStore(path, output_root=tmp_path) as store:
        stats = ingest(store, candles + [candles[0]])
        assert stats["inserted_rows"] == 375
        assert stats["duplicate_payload_rows"] == 1
        assert store.coverage([CODE], [DAY])["complete_sessions"] == 1
    with StageStore(path, output_root=tmp_path) as store:
        stats = ingest(store, candles)
        assert stats["inserted_rows"] == 0
        assert stats["duplicate_stored_rows"] == 375
        assert store.coverage([CODE], [DAY])["by_code"][CODE]["complete_sessions"] == [str(DAY)]
        assert store.con.execute("SELECT count(*) FROM aem_stage_ingests").fetchone()[0] == 2


def test_empty_partial_and_missing_code_never_complete(store):
    assert ingest(store, [])["inserted_rows"] == 0
    ingest(store, [bar(i) for i in range(374)])
    state = store.coverage([CODE, "NSE_1333"], [DAY])
    assert state["complete_sessions"] == 0
    assert state["missing_sessions"] == 2
    assert state["total_rows"] == 374
    assert state["by_code"]["NSE_1333"]["missing_sessions"] == [str(DAY)]
    ingest(store, [bar(374)])
    assert store.coverage([CODE], [DAY])["complete_sessions"] == 1


async def test_sequential_worker_thread_calls_share_connection_safely(store):
    first = await asyncio.to_thread(ingest, store, [bar(i) for i in range(374)])
    assert first["inserted_rows"] == 374
    partial = await asyncio.to_thread(store.coverage, [CODE], [DAY])
    assert partial["complete_sessions"] == 0
    second = await asyncio.to_thread(ingest, store, [bar(374)])
    assert second["inserted_rows"] == 1
    complete = await asyncio.to_thread(store.coverage, [CODE], [DAY])
    assert complete["complete_sessions"] == 1


def test_filters_extra_dates_non_sessions_and_endpoint_marker(store):
    stats = ingest(
        store,
        [
            bar(),
            bar(-1),
            bar(375),
            bar(ts=OPEN - timedelta(days=1)),
            bar(ts=OPEN + timedelta(days=1)),
        ],
        end_exclusive=DAY + timedelta(days=2),
    )
    assert stats["inserted_rows"] == 1
    assert stats["outside_regular_rows"] == 2
    assert stats["out_of_window_rows"] == 1
    assert stats["non_session_rows"] == 1


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"ts": OPEN.replace(tzinfo=None)}, "timezone"),
        ({"ts": OPEN.replace(second=1)}, "exact_minute"),
        ({"ts": OPEN.replace(microsecond=1)}, "exact_minute"),
        ({"interval": Interval.M5}, "interval"),
        ({"scrip_code": "NSE_1333"}, "code"),
        ({"open": float("nan")}, "finite"),
        ({"high": float("inf")}, "finite"),
        ({"low": 0}, "positive"),
        ({"high": 99}, "geometry"),
        ({"low": 101}, "geometry"),
        ({"volume": -1}, "volume"),
        ({"volume": 0.5}, "volume"),
        ({"volume": float("nan")}, "volume"),
        ({"volume": 2**63}, "volume"),
        ({"volume": True}, "type"),
    ],
)
def test_invalid_batch_is_atomic(store, updates, match):
    with pytest.raises(ValueError, match=match):
        ingest(store, [bar(1), bar(**updates)])
    assert store.con.execute("SELECT count(*) FROM candles").fetchone()[0] == 0
    assert store.con.execute("SELECT count(*) FROM aem_stage_ingests").fetchone()[0] == 0


def test_unknown_response_code_even_if_empty_rejected(store):
    with pytest.raises(ValueError, match="response_code"):
        store.ingest(
            {"NSE_1333": []},
            codes=[CODE],
            start=DAY,
            end_exclusive=DAY + timedelta(days=1),
            sessions=[DAY],
            origin="test",
        )


def test_conflicting_payload_and_stored_values_fail_whole_batch(store):
    with pytest.raises(ValueError, match="conflicting_payload"):
        ingest(store, [bar(), bar(close=100), bar(1)])
    assert store.coverage([CODE], [DAY])["total_rows"] == 0
    ingest(store, [bar()])
    with pytest.raises(ValueError, match="conflicting_stored"):
        ingest(store, [bar(1), bar(close=100)])
    assert store.coverage([CODE], [DAY])["total_rows"] == 1
    assert store.con.execute("SELECT close FROM candles").fetchone()[0] == 101
    assert store.con.execute("SELECT count(*) FROM aem_stage_ingests").fetchone()[0] == 1


def test_utc_bar_is_normalized_without_shifting_slot(store):
    ingest(store, [bar(ts=OPEN.astimezone(UTC))])
    assert store.con.execute("SELECT ts FROM candles").fetchone()[0] == int(OPEN.timestamp())


def test_coverage_requires_expected_slots_not_count(store):
    ingest(store, [bar(i) for i in range(375)])
    store.con.execute("UPDATE candles SET ts=ts+1 WHERE ts=?", [int(OPEN.timestamp())])
    state = store.coverage([CODE], [DAY])
    assert state["total_rows"] == 375
    assert state["complete_sessions"] == 0
    assert state["invalid_rows"] == 1


def test_coverage_revalidates_prices_not_just_slots(store):
    ingest(store, [bar(i) for i in range(375)])
    store.con.execute("UPDATE candles SET high=0 WHERE ts=?", [int(OPEN.timestamp())])
    assert store.coverage([CODE], [DAY])["complete_sessions"] == 0


def test_coverage_empty_calendar_and_unselected_dates(store):
    ingest(store, [bar()])
    assert store.coverage([CODE], [])["expected_rows"] == 0
    assert store.coverage([CODE], [DAY + timedelta(days=1)])["total_rows"] == 0


def test_refuses_outside_root_and_existing_production_database(tmp_path):
    source = tmp_path / "production.duckdb"
    with duckdb.connect(str(source)) as con:
        con.execute("CREATE TABLE production (important INT)")
        con.execute("INSERT INTO production VALUES (42)")
    before = source.read_bytes()
    with pytest.raises(ValueError, match="inside_output_root"):
        StageStore(source, output_root=tmp_path, source_path=source)
    research_source = tmp_path / "aem_history/source.duckdb"
    research_source.parent.mkdir()
    research_source.write_bytes(before)
    with pytest.raises(ValueError, match="source_database"):
        StageStore(research_source, output_root=tmp_path, source_path=research_source)
    with pytest.raises(ValueError, match="not_an_aem_stage"):
        StageStore(research_source, output_root=tmp_path)
    assert research_source.read_bytes() == before
    assert source.read_bytes() == before


def test_rejects_parent_traversal(tmp_path):
    with pytest.raises(ValueError, match="inside_output_root"):
        StageStore(tmp_path / "aem_history/../source.duckdb", output_root=tmp_path)


def test_rejects_hardlink_alias(tmp_path):
    folder = tmp_path / "aem_history"
    folder.mkdir()
    original = folder / "original.duckdb"
    with StageStore(original, output_root=tmp_path):
        pass
    alias = folder / "alias.duckdb"
    try:
        alias.hardlink_to(original)
    except OSError:
        pytest.skip("hardlinks unavailable")
    with pytest.raises(ValueError, match="single_link"):
        StageStore(alias, output_root=tmp_path)


def test_rejects_symlink_alias_even_within_lab(tmp_path):
    real = tmp_path / "aem_history/real"
    real.mkdir(parents=True)
    alias = tmp_path / "aem_history/alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="symlink"):
        StageStore(alias / "stage.duckdb", output_root=tmp_path)


@pytest.mark.parametrize(
    "codes,sessions", [([CODE, CODE], [DAY]), ([CODE], [DAY, DAY]), ([], [DAY]), ([CODE], [OPEN])]
)
def test_rejects_ambiguous_coverage_request(store, codes, sessions):
    with pytest.raises(ValueError):
        store.coverage(codes, sessions)
