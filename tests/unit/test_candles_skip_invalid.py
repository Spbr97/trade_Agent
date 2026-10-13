"""candles_history(skip_invalid=True) drops only the rejected code, not its batch."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.broker.indstocks.rest import ApiError, IndstocksClient


class _Fake(IndstocksClient):
    def __init__(self) -> None:  # no HTTP, no credentials
        self.calls: list[list[str]] = []

    async def candles(self, interval, scrip_codes, start, end):  # type: ignore[no-untyped-def]
        self.calls.append(list(scrip_codes))
        if "NSE_BAD" in scrip_codes:
            raise ApiError(400, "Invalid scrip codes", "x")
        return {c: [] for c in scrip_codes}


def _run(skip: bool) -> tuple[dict, _Fake]:
    fake = _Fake()
    end = datetime(2026, 10, 13, 12, tzinfo=IST)
    codes = ["NSE_1", "NSE_2", "NSE_BAD", "NSE_3", "NSE_4"]
    got = asyncio.run(
        fake.candles_history(
            Interval.M15, codes, end - timedelta(days=1), end, skip_invalid=skip
        )
    )
    return got, fake


def test_skip_invalid_keeps_healthy_neighbours() -> None:
    got, fake = _run(True)
    assert set(got) == {"NSE_1", "NSE_2", "NSE_BAD", "NSE_3", "NSE_4"}
    assert len(fake.calls) > 1


def test_default_still_raises_so_the_loader_can_track_dead_scrips() -> None:
    with pytest.raises(ApiError):
        _run(False)
