"""dead_scrips + the loader's bisect: one invalid code must not starve its batch-mates."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.data import dead_scrips as ds
from tradedesk.data.history_loader import load_history


def test_skipped_only_after_three_lone_failures_and_retried_after_30_days(tmp_path: Path) -> None:
    p = tmp_path / "dead.json"
    day = date(2026, 9, 1)
    for i in range(2):
        ds.record(p, day + timedelta(days=i), invalid=["NSE_1"], succeeded=[])
    assert ds.skip_set(p, day + timedelta(days=2)) == set()  # only 2 failures
    ds.record(p, day + timedelta(days=2), invalid=["NSE_1"], succeeded=[])
    assert ds.skip_set(p, day + timedelta(days=3)) == {"NSE_1"}
    assert ds.skip_set(p, day + timedelta(days=2 + ds.RETRY_DAYS)) == set()  # retry window


def test_a_code_that_loads_fine_is_forgotten(tmp_path: Path) -> None:
    p = tmp_path / "dead.json"
    for i in range(3):
        ds.record(p, date(2026, 9, 1 + i), invalid=["NSE_1"], succeeded=[])
    ds.record(p, date(2026, 9, 10), invalid=[], succeeded=["NSE_1"])
    assert ds.skip_set(p, date(2026, 9, 11)) == set()


def test_missing_or_corrupt_file_skips_nothing(tmp_path: Path) -> None:
    assert ds.skip_set(tmp_path / "none.json", date(2026, 9, 1)) == set()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert ds.skip_set(bad, date(2026, 9, 1)) == set()


class _ApiError(Exception):
    pass


class _FakeClient:
    """Rejects the whole call if any code in it is invalid, like the real API."""

    def __init__(self, bad: set[str]) -> None:
        self.bad = bad
        self.calls: list[list[str]] = []

    async def candles_history(self, interval, codes, start, end):  # noqa: ANN001
        self.calls.append(list(codes))
        if self.bad & set(codes):
            raise _ApiError("HTTP 400 : Invalid scrip codes")
        return {c: [] for c in codes}


class _FakeStore:
    def last_ts(self, code, interval):  # noqa: ANN001
        return None

    def upsert_candles(self, candles):  # noqa: ANN001
        return len(candles)


def test_bisect_isolates_the_bad_code_and_healthy_neighbours_load() -> None:
    codes = ["NSE_1", "NSE_2", "NSE_3", "NSE_4", "NSE_5"]
    client = _FakeClient(bad={"NSE_3"})
    summary = asyncio.run(
        load_history(
            client, _FakeStore(), codes, Interval.D1,  # type: ignore[arg-type]
            start=datetime.now(IST) - timedelta(days=5), error_types=(_ApiError,),
        )
    )  # fmt: skip
    errors = {r.scrip_code for r in summary.results if r.error}
    assert errors == {"NSE_3"}
    assert {r.scrip_code for r in summary.results if not r.error} == set(codes) - {"NSE_3"}
