import json
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT
from tradedesk_lab.aem_universe_plan import freeze_universe_plan

IDENTIFIER = "a" * 32


def _setup(tmp_path):
    dates = pd.bdate_range("2025-01-01", periods=65, tz="Asia/Kolkata")
    output = tmp_path / "outputs"
    folder = output / "aem/datasets" / IDENTIFIER
    folder.mkdir(parents=True)
    manifest = {
        "id": IDENTIFIER,
        "contract": DEFAULT_AEM_CONTRACT.to_dict(),
        "contract_sha256": DEFAULT_AEM_CONTRACT.sha256,
        "source": {
            "benchmark_calendar": [str(day.date()) for day in dates],
            "evaluation_dates": [str(day.date()) for day in dates[-5:]],
        },
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    database = tmp_path / "data/tradedesk.duckdb"
    database.parent.mkdir()
    con = duckdb.connect(str(database))
    con.execute(
        "CREATE TABLE instruments(scrip_code VARCHAR, exch VARCHAR, kind VARCHAR, "
        "trading_symbol VARCHAR, series VARCHAR, instrument_name VARCHAR)"
    )
    con.execute(
        "CREATE TABLE candles(scrip_code VARCHAR, interval VARCHAR, ts BIGINT, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)"
    )
    return output, database, con, dates


def _stock(con, dates, code, *, volume=1_000_000, missing=(), exchange="NSE", series="EQ"):
    con.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?)",
        [code, exchange, "equity", code, series, "EQUITY"],
    )
    # Falling prices fail AEM trend, deliberately: selection must not use strategy eligibility.
    con.executemany(
        "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
        [
            (code, "1day", int(day.timestamp()), 200 - i, 201 - i, 199 - i, 200 - i, volume)
            for i, day in enumerate(dates)
            if i not in missing
        ],
    )


def _run(tmp_path, output, **kwargs):
    return freeze_universe_plan(tmp_path, output, dataset_id=IDENTIFIER, **kwargs)


def test_preperiod_liquidity_only_selection_and_m1_warmup_plan(tmp_path):
    output, database, con, dates = _setup(tmp_path)
    _stock(con, dates, "NSE_UNSEEN1")
    _stock(con, dates, "NSE_UNSEEN2", volume=2_000_000)
    _stock(con, dates, "NSE_UNSEEN3", volume=100)
    _stock(con, dates, "BSE_OTHER", exchange="BSE")
    con.close()
    before = database.read_bytes()
    report = _run(tmp_path, output, shortlist_size=1)
    assert database.read_bytes() == before
    assert report["freeze_on"] == str(dates[-6].date())
    assert report["summary"]["liquidity_candidates"] == 2
    assert [row["scrip_code"] for row in report["shortlist"]] == ["NSE_UNSEEN2"]
    assert report["selection_rule"]["setup_eligibility_used"] is False
    assert report["selection_rule"]["intraday_availability_used"] is False
    assert report["eligible_for_live"] is False
    assert report["summary"]["rejection_reasons"] == {
        "insufficient_preperiod_liquidity": 1,
        "not_nse": 1,
    }
    plan = report["backfill_plan"]
    assert plan["mode"] == "plan_only_no_network"
    assert plan["interval"] == "1minute"
    assert plan["warmup_sessions"] == 20
    assert plan["total_sessions"] == 25
    assert plan["start"] == str(dates[-25].date())
    assert plan["end_exclusive"] == str(dates[-1].date() + timedelta(days=1))
    assert plan["expected_regular_m1_bars"] == 25 * 375
    assert report["coverage"][0]["intervals"][0]["complete_sessions"] == 0
    assert len(report["coverage"][0]["intervals"]) == 1
    assert Path(report["artifact_path"]).is_file()
    second = _run(tmp_path, output, shortlist_size=1)
    assert report["selection_sha256"] == second["selection_sha256"]
    assert report["artifact_path"] != second["artifact_path"]


def test_post_freeze_prices_and_intraday_availability_cannot_change_selection(tmp_path):
    output, database, con, dates = _setup(tmp_path)
    _stock(con, dates, "NSE_1")
    _stock(con, dates, "NSE_2", volume=2_000_000)
    con.close()
    first = _run(tmp_path, output)
    with duckdb.connect(str(database)) as con:
        con.execute(
            "UPDATE candles SET close=1e9,volume=1000000000 WHERE scrip_code='NSE_1' AND ts>=?",
            [int(dates[-5].timestamp())],
        )
        session = dates[-1] + pd.Timedelta(hours=9, minutes=15)
        con.executemany(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    "NSE_1",
                    "1minute",
                    int((session + pd.Timedelta(minutes=i)).timestamp()),
                    100,
                    101,
                    99,
                    100,
                    1000,
                )
                for i in range(375)
            ],
        )
    second = _run(tmp_path, output)
    assert first["selection_source_sha256"] == second["selection_source_sha256"]
    assert first["selection_sha256"] == second["selection_sha256"]
    assert first["shortlist"] == second["shortlist"]
    assert first["plan_sha256"] != second["plan_sha256"]


def test_audits_stale_missing_warmup_invalid_and_later_listing(tmp_path):
    output, _, con, dates = _setup(tmp_path)
    _stock(con, dates, "NSE_BASE")
    _stock(con, dates, "NSE_GAP", missing=(55,))
    _stock(con, dates, "NSE_STALE", missing=(59,))
    _stock(con, dates, "NSE_NEW", missing=tuple(range(60)))
    _stock(con, dates, "NSE_SHORT", missing=(0,))
    _stock(con, dates, "NSE_BAD")
    con.execute("UPDATE candles SET high=0 WHERE scrip_code='NSE_BAD'")
    con.close()
    report = _run(tmp_path, output)
    audit = {row["scrip_code"]: row for row in report["instrument_audit"]}
    assert audit["NSE_GAP"]["reasons"] == ["incomplete_recent_daily_sessions"]
    assert audit["NSE_STALE"]["reasons"] == ["stale_daily_close"]
    assert audit["NSE_NEW"]["reasons"] == ["no_daily_history_as_of"]
    assert audit["NSE_SHORT"]["reasons"] == ["insufficient_daily_warmup"]
    assert audit["NSE_BAD"]["reasons"] == ["invalid_daily_data"]
    assert len(report["shortlist"]) == 1


def test_request_batching_is_bounded_and_conservative_count_retained(tmp_path):
    output, _, con, dates = _setup(tmp_path)
    for i in range(6):
        _stock(con, dates, f"NSE_{i}")
    con.close()
    report = _run(tmp_path, output)
    plan = report["backfill_plan"]
    assert (
        plan["planned_missing_window_requests_one_code"]
        == 3 * plan["planned_missing_window_requests_batched"]
    )
    for batch in plan["request_batches"]:
        assert 1 <= len(batch["scrip_codes"]) <= 5
        assert 0 < (pd.Timestamp(batch["end_exclusive"]) - pd.Timestamp(batch["start"])).days <= 7


@pytest.mark.parametrize("identifier", ["../x", "a" * 31, "A" * 32, None])
def test_rejects_unsafe_identifiers(tmp_path, identifier):
    with pytest.raises(ValueError, match="identifier"):
        freeze_universe_plan(tmp_path, tmp_path, dataset_id=identifier)


def test_validates_manifest_dates_contract_and_size(tmp_path):
    output, _, con, _ = _setup(tmp_path)
    con.close()
    for size in (0, 501, True, 1.5):
        with pytest.raises(ValueError, match="shortlist_size"):
            _run(tmp_path, output, shortlist_size=size)
    path = output / "aem/datasets" / IDENTIFIER / "manifest.json"
    manifest = json.loads(path.read_text())
    original = json.loads(path.read_text())
    manifest["contract_sha256"] = "invalid"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="contract hash"):
        _run(tmp_path, output)
    original["source"]["evaluation_dates"].reverse()
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="sorted and unique"):
        _run(tmp_path, output)


def test_source_lock_is_durable_blocked_report(tmp_path, monkeypatch):
    output, _, con, _ = _setup(tmp_path)
    con.close()

    def locked(*args, **kwargs):
        raise duckdb.IOException("source held by the live writer")

    monkeypatch.setattr("tradedesk_lab.aem_universe_plan.duckdb.connect", locked)
    report = _run(tmp_path, output)
    assert report["status"] == "blocked_source_unavailable"
    assert "shortlist" not in report
    assert Path(report["artifact_path"]).is_file()
