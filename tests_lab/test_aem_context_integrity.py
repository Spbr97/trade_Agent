import json
from datetime import date, datetime, time, timedelta

import duckdb
import pandas as pd
import pytest
from tradedesk_lab.aem_context_integrity import (
    OUTCOME_COLUMNS,
    context_snapshot,
    join_context_events,
    run_context_integrity,
)
from tradedesk_lab.artifacts import digest

from tradedesk.broker.indstocks.models import IST

SYMBOLS = {
    "NIFTY 50": "NSE_40000001",
    "BANK NIFTY": "NSE_40000003",
    "Nifty Financial": "NSE_40000100",
}


def _frame(*, minutes=16, missing=None, zero_volume=False):
    start = pd.Timestamp("2026-07-07 09:15", tz=IST)
    index = pd.date_range(start, periods=minutes, freq="min")
    if missing is not None:
        index = index.delete(missing)
    values = []
    for offset, _ in enumerate(index):
        opening = 100.0 + offset * 0.1
        values.append(
            {
                "open": opening,
                "high": opening + 0.06,
                "low": opening - 0.01,
                "close": opening + 0.05,
                "volume": 0 if zero_volume else 100 + offset,
            }
        )
    return pd.DataFrame(values, index=index)


def _frames(*, minutes=16, missing=None, zero_financial=False):
    return {
        code: _frame(
            minutes=minutes,
            missing=missing if code == "NSE_40000100" else None,
            zero_volume=zero_financial and code == "NSE_40000100",
        )
        for code in SYMBOLS.values()
    }


def _events():
    return pd.DataFrame(
        [
            {
                "event_id": "e1",
                "scrip_code": "NSE_1",
                "session_date": "2026-07-07",
                "decision": "TRADE",
                "available_at": "2026-07-07T09:20:00+05:30",
                "status": "resolved",
                "label": 1,
                "strict_success": True,
                "net_r": 0.5,
            },
            {
                "event_id": "e2",
                "scrip_code": "NSE_2",
                "session_date": "2026-07-07",
                "decision": "NO_TRADE",
                "available_at": "2026-07-07T09:30:00+05:30",
                "status": "",
                "label": "",
                "strict_success": "",
                "net_r": "",
            },
        ]
    )


def test_snapshot_uses_only_completed_bars_and_ignores_future_changes():
    frames = _frames()
    first = context_snapshot(
        frames, decision_at="2026-07-07T09:20:00+05:30", symbols=SYMBOLS
    )
    changed = {code: frame.copy() for code, frame in frames.items()}
    for frame in changed.values():
        frame.loc[pd.Timestamp("2026-07-07 09:20", tz=IST), ["open", "high", "low", "close"]] = [
            900,
            901,
            899,
            900.5,
        ]
    second = context_snapshot(
        changed, decision_at="2026-07-07T09:20:00+05:30", symbols=SYMBOLS
    )
    assert first == second
    assert first["context_joined"] is True
    assert first["context_cutoff"].endswith("09:19:00+05:30")
    assert first["context_return_5m_available"] is True
    assert first["context_return_15m_available"] is False


def test_missing_completed_bar_fails_closed_with_explicit_reason():
    result = context_snapshot(
        _frames(missing=3),
        decision_at="2026-07-07T09:20:00+05:30",
        symbols=SYMBOLS,
    )
    assert result["context_joined"] is False
    assert result["context_exclusion_reason"] == (
        "missing_context_prefix:nifty_financial:09:18"
    )


def test_join_accounts_for_events_without_copying_outcomes_and_vwap_fails_closed():
    joined, audit = join_context_events(
        _events(),
        _frames(zero_financial=True),
        symbols=SYMBOLS,
        sessions=["2026-07-07"],
        earliest_decision_time="09:20",
        latest_decision_time="11:00",
    )
    assert audit == {
        "events_total": 2,
        "trades": 1,
        "no_trades": 1,
        "joined": 2,
        "excluded": 0,
        "exclusion_reasons": {},
        "outcome_columns_present": [],
    }
    assert not (set(joined) & OUTCOME_COLUMNS)
    assert joined.context_joined.all()
    assert joined.context_vwap_available.tolist() == [False, False]
    assert joined.context_return_15m_available.tolist() == [False, True]


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("available_at", "2026-07-07 09:20", "timezone-aware"),
        ("available_at", "2026-07-07T09:20:01+05:30", "exact minute"),
        ("session_date", "2026-07-08", "outside the frozen calendar"),
    ],
)
def test_join_rejects_invalid_event_coordinates(field, value, message):
    events = _events().iloc[:1].copy()
    events.loc[0, field] = value
    with pytest.raises(ValueError, match=message):
        join_context_events(
            events,
            _frames(),
            symbols=SYMBOLS,
            sessions=["2026-07-07"],
            earliest_decision_time="09:20",
            latest_decision_time="11:00",
        )


def _disk_source(tmp_path):
    root, output = tmp_path / "root", tmp_path / "output"
    dataset_id = "a" * 32
    dataset = output / "aem_staged/datasets" / dataset_id
    context = output / "aem_history/context" / dataset_id
    dataset.mkdir(parents=True)
    context.mkdir(parents=True)
    research_plan = root / "docs/plan-aem-index-context-accuracy.md"
    research_plan.parent.mkdir(parents=True)
    research_plan.write_text("# frozen test plan\n", encoding="utf-8")
    integrity_test = root / "tests_lab/test_aem_context_integrity.py"
    integrity_test.parent.mkdir(parents=True)
    integrity_test.write_text("# frozen test fixture\n", encoding="utf-8")

    events = _events()
    events.to_csv(dataset / "events.csv", index=False)
    manifest = {
        "id": dataset_id,
        "events": len(events),
        "contract": {
            "earliest_decision_time": "09:20",
            "latest_decision_time": "11:00",
        },
    }
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = {
        "version": "aem-context-history-v1",
        "dataset_id": dataset_id,
        "dataset_manifest_sha256": digest(dataset / "manifest.json"),
        "symbols": SYMBOLS,
        "sessions": ["2026-07-07"],
    }
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    plan["sha256"] = __import__("hashlib").sha256(payload.encode()).hexdigest()
    (context / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    rows = []
    for code_index, code in enumerate(SYMBOLS.values()):
        zero_volume = code == "NSE_40000100"
        start = datetime.combine(date(2026, 7, 7), time(9, 15), IST)
        for offset in range(375):
            stamp = start + timedelta(minutes=offset)
            opening = 100 + code_index * 10 + offset * 0.01
            rows.append(
                (
                    code,
                    "1minute",
                    int(stamp.timestamp()),
                    opening,
                    opening + 0.06,
                    opening - 0.01,
                    opening + 0.05,
                    0 if zero_volume else 100 + offset,
                )
            )
    database = context / "candles.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute(
            "CREATE TABLE candles (scrip_code VARCHAR, interval VARCHAR, ts BIGINT, "
            "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, "
            "PRIMARY KEY (scrip_code, interval, ts))"
        )
        con.execute("CREATE TABLE aem_stage_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
        con.execute("INSERT INTO aem_stage_meta VALUES ('schema_version','1')")
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)
    return root, output, dataset_id


def test_real_runner_writes_feature_only_integrity_artifacts(tmp_path):
    root, output, dataset_id = _disk_source(tmp_path)
    report = run_context_integrity(root, output, dataset_id=dataset_id)
    assert report["status"] == "integrity_passed"
    assert report["population"]["joined"] == 2
    assert report["population"]["excluded"] == 0
    assert report["feature_availability"]["return_15m_all_indices"] == 1
    assert report["feature_availability"]["vwap_all_indices"] == 0
    assert report["source_gaps"] == []
    assert report["decision"]["price_context_ready_for_mechanism_check"] is True
    assert report["decision"]["vwap_context_ready"] is False
    feature_path = report["artifacts"]["context_features"]
    written = pd.read_csv(feature_path)
    assert len(written) == 2
    assert not (set(written) & OUTCOME_COLUMNS)


def test_real_runner_preserves_all_join_exclusions_instead_of_crashing(tmp_path):
    root, output, dataset_id = _disk_source(tmp_path)
    database = output / "aem_history/context" / dataset_id / "candles.duckdb"
    missing = int(pd.Timestamp("2026-07-07 09:19", tz=IST).timestamp())
    with duckdb.connect(str(database)) as con:
        con.execute(
            "DELETE FROM candles WHERE scrip_code='NSE_40000100' AND ts=?", [missing]
        )
    report = run_context_integrity(root, output, dataset_id=dataset_id)
    assert report["status"] == "integrity_exclusions"
    assert report["population"]["joined"] == 0
    assert report["population"]["excluded"] == 2
    assert report["feature_availability"]["return_5m_all_indices"] == 0
    assert report["decision"]["price_context_ready_for_mechanism_check"] is False
