import json
import os
from datetime import date, datetime, time, timedelta

import duckdb
import pytest
from tests_lab.test_aem_universe_plan import IDENTIFIER, _setup, _stock
from tradedesk_lab.aem_history import (
    _collection_lock,
    _write_checkpoint,
    collect_history,
    load_collection_plan,
)
from tradedesk_lab.aem_universe_plan import freeze_universe_plan

from tradedesk.broker.indstocks.models import IST, Candle, Interval


def _plan(tmp_path, count=1):
    output, database, con, dates = _setup(tmp_path)
    for index in range(count):
        _stock(con, dates, f"NSE_{index + 1}")
    con.close()
    plan = freeze_universe_plan(tmp_path, output, dataset_id=IDENTIFIER)
    return output, database, plan


def _bars(code, day):
    start = datetime.combine(day, time(9, 15), IST)
    return [
        Candle(
            scrip_code=code,
            interval=Interval.M1,
            ts=start + timedelta(minutes=i),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1000,
        )
        for i in range(375)
    ]


class FakeClient:
    instances = []
    partial = False

    def __init__(self, *, max_requests):
        self.budget = max_requests
        self.request_count = 0
        self.calls = []
        self.closed = False
        self.instances.append(self)

    async def candles(self, codes, start, end):
        assert self.request_count < self.budget
        self.request_count += 1
        self.calls.append((codes, start, end))
        values = {code: [] for code in codes}
        day = start.date()
        while day < end.date():
            if day.weekday() < 5:
                for code in codes:
                    values[code].extend(_bars(code, day)[: 1 if self.partial else 375])
            day += timedelta(days=1)
        return values

    async def aclose(self):
        self.closed = True


def _run(tmp_path, output, plan, budget=0, **kwargs):
    return collect_history(
        tmp_path,
        output,
        plan_id=plan["id"],
        max_requests=budget,
        client_factory=FakeClient,
        preflight=kwargs.pop("preflight", lambda: {"allowed": True}),
        **kwargs,
    )


def test_plan_verifies_full_fingerprint_and_rejects_unhashed_batch_edits(tmp_path):
    output, _, plan = _plan(tmp_path)
    validated, sha = load_collection_plan(output, plan["id"])
    assert validated == plan
    assert len(sha) == 64
    path = output / "aem_universe/runs" / plan["id"] / "report.json"
    plan["backfill_plan"]["request_batches"][0]["scrip_codes"] = ["NSE_999"]
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="request batches"):
        load_collection_plan(output, plan["id"])


@pytest.mark.parametrize("identifier", ["../escape", "", "A" * 32, None])
def test_bad_plan_identifiers(tmp_path, identifier):
    with pytest.raises(ValueError, match="identifier"):
        load_collection_plan(tmp_path, identifier)


def test_offline_seed_never_creates_client_and_does_not_change_source(tmp_path, monkeypatch):
    output, database, plan = _plan(tmp_path)
    day = date.fromisoformat(plan["warmup_dates"][0])
    with duckdb.connect(str(database)) as con:
        con.executemany(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    c.scrip_code,
                    c.interval.value,
                    int(c.ts.timestamp()),
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                )
                for c in _bars("NSE_1", day)
            ],
        )
    before = database.read_bytes()
    monkeypatch.setattr(FakeClient, "instances", [])
    report = _run(tmp_path, output, plan)
    assert report["status"] == "prepared_offline"
    assert report["requests_this_run"] == 0
    assert report["coverage"]["complete_sessions"] == 1
    assert not FakeClient.instances
    assert database.read_bytes() == before
    assert report["eligible_for_live"] is False
    assert report["evaluation_ready"] is False


def test_bounded_collection_resume_skips_complete_and_reaches_coverage(tmp_path):
    output, database, plan = _plan(tmp_path, count=2)
    before = database.read_bytes()
    first = _run(tmp_path, output, plan, 1)
    assert first["requests_this_run"] == 1
    assert first["coverage"]["complete_sessions"] > 0
    assert FakeClient.instances[-1].closed
    second = _run(tmp_path, output, plan, 100)
    assert second["status"] == "regular_m1_coverage_complete"
    assert second["requests_this_run"] == len(plan["backfill_plan"]["request_batches"]) - 1
    assert second["coverage"]["missing_sessions"] == 0
    third = _run(tmp_path, output, plan, 10)
    assert third["requests_this_run"] == 0
    assert database.read_bytes() == before


def test_partial_response_not_complete_and_remains_retryable(tmp_path, monkeypatch):
    output, _, plan = _plan(tmp_path)
    monkeypatch.setattr(FakeClient, "partial", True)
    partial = _run(tmp_path, output, plan, 100)
    assert partial["status"] == "frozen_requests_exhausted"
    assert partial["coverage"]["complete_sessions"] == 0
    assert all(a["status"] == "partial" for a in partial["attempts"])
    monkeypatch.setattr(FakeClient, "partial", False)
    full = _run(tmp_path, output, plan, 100)
    assert full["status"] == "regular_m1_coverage_complete"


def test_preflight_stops_before_credentials_and_rechecks_between_batches(tmp_path, monkeypatch):
    output, _, plan = _plan(tmp_path)
    monkeypatch.setattr(FakeClient, "instances", [])
    blocked = _run(tmp_path, output, plan, 3, preflight=lambda: {"allowed": False})
    assert blocked["status"] == "blocked_preflight"
    assert blocked["requests_this_run"] == 0
    assert not FakeClient.instances
    guards = iter([{"allowed": True}, {"allowed": True}, {"allowed": False}])
    report = _run(tmp_path, output, plan, 3, preflight=lambda: next(guards))
    assert report["requests_this_run"] == 1
    assert report["status"] == "blocked_preflight"


def test_resume_detects_full_artifact_mutation(tmp_path):
    output, _, plan = _plan(tmp_path)
    _run(tmp_path, output, plan)
    plan["limitations"].append("changed after checkpoint")
    path = output / "aem_universe/runs" / plan["id"] / "report.json"
    path.write_text(json.dumps(plan))
    blocked = _run(tmp_path, output, plan)
    assert blocked["status"] == "blocked_preparation"
    assert blocked["requests_this_run"] == 0


def test_lock_excludes_concurrent_collector_and_does_not_delete_existing_lock(tmp_path):
    output, _, plan = _plan(tmp_path)
    _run(tmp_path, output, plan)
    parent = output / "aem_history"
    with _collection_lock(parent):
        blocked = _run(tmp_path, output, plan, 2)
        assert blocked["status"] == "blocked_preparation"
        assert (parent / "collector.lock").exists()
    assert not (parent / "collector.lock").exists()


def test_source_unavailable_can_resume_without_false_seed_success(tmp_path, monkeypatch):
    output, _, plan = _plan(tmp_path)
    from tradedesk_lab import aem_history

    original = aem_history._seed_source

    def locked(*args):
        raise duckdb.IOException("source locked")

    monkeypatch.setattr(aem_history, "_seed_source", locked)
    blocked = _run(tmp_path, output, plan)
    assert blocked["status"] == "blocked_preparation"
    monkeypatch.setattr(aem_history, "_seed_source", original)
    resumed = _run(tmp_path, output, plan)
    assert resumed["status"] == "prepared_offline"


@pytest.mark.parametrize("budget", [-1, 101, True, 1.5])
def test_rejects_unbounded_request_budgets(tmp_path, budget):
    with pytest.raises(ValueError, match="max_requests"):
        collect_history(tmp_path, tmp_path, plan_id="a" * 32, max_requests=budget)


def test_pending_crash_attempt_is_reported_as_unknown_not_zero_usage(tmp_path):
    output, _, plan = _plan(tmp_path)
    first = _run(tmp_path, output, plan)
    state_path = output / "aem_history" / plan["id"] / "state.json"
    state = json.loads(state_path.read_text())
    state["attempts"].append({"status": "pending", "batch_index": 0})
    state_path.write_text(json.dumps(state))
    resumed = _run(tmp_path, output, plan)
    assert first["unconfirmed_prior_attempts"] == 0
    assert resumed["unconfirmed_prior_attempts"] == 1
    assert resumed["requests_recorded_all_runs"] == 0  # known records, not an account ledger


def test_checkpoint_temp_hardlink_cannot_overwrite_another_file(tmp_path):
    sentinel = tmp_path / "must_not_change.json"
    sentinel.write_text("original content")
    target = tmp_path / "state.json"
    os.link(sentinel, target.with_suffix(".json.tmp"))
    with pytest.raises(ValueError, match="hard links"):
        _write_checkpoint(target, {"seed": "different"})
    assert sentinel.read_text() == "original content"
    assert not target.exists()


def test_seed_loss_outside_frozen_windows_blocks_without_new_requests(tmp_path):
    output, database, con, dates = _setup(tmp_path)
    _stock(con, dates, "NSE_1")
    day = dates[-25].date()
    con.executemany(
        "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                c.scrip_code,
                c.interval.value,
                int(c.ts.timestamp()),
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            )
            for session in dates[-25:-20]
            for c in _bars("NSE_1", session.date())
        ],
    )
    con.close()
    plan = freeze_universe_plan(tmp_path, output, dataset_id=IDENTIFIER)
    with duckdb.connect(str(database)) as con:
        con.execute("DELETE FROM candles WHERE interval='1minute'")
    blocked = _run(tmp_path, output, plan, 5)
    assert blocked["status"] == "blocked_source_plan_drift"
    assert blocked["requests_this_run"] == 0
    assert str(day) in blocked["gaps_outside_plan"]["NSE_1"]
