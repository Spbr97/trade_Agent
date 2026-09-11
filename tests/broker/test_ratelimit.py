import asyncio
import time
from datetime import datetime

import pytest

from tradedesk.broker.indstocks.models import IST
from tradedesk.broker.indstocks.ratelimit import Category, DailyLimitExceeded, RateLimiter


async def test_per_second_limit_is_enforced() -> None:
    limiter = RateLimiter()
    t0 = time.monotonic()
    # 5 per second: 11 calls need at least ~1.2 s of wall time (bursts of 5, 5, 1).
    await asyncio.gather(*(limiter.acquire(Category.DATA) for _ in range(11)))
    elapsed = time.monotonic() - t0
    assert elapsed >= 1.0


async def test_daily_cap_fails_closed() -> None:
    limiter = RateLimiter(daily_cap=3)
    now = datetime(2026, 9, 11, 10, 0, tzinfo=IST)
    for _ in range(3):
        await limiter.acquire(Category.QUOTE, now=now)
    assert limiter.used_today(Category.QUOTE, now) == 3
    with pytest.raises(DailyLimitExceeded):
        await limiter.acquire(Category.QUOTE, now=now)
    # Other categories have their own budget.
    await limiter.acquire(Category.DATA, now=now)


async def test_daily_counter_resets_on_new_ist_day() -> None:
    limiter = RateLimiter(daily_cap=1)
    d1 = datetime(2026, 9, 11, 23, 59, tzinfo=IST)
    d2 = datetime(2026, 9, 12, 0, 1, tzinfo=IST)
    await limiter.acquire(Category.DATA, now=d1)
    with pytest.raises(DailyLimitExceeded):
        await limiter.acquire(Category.DATA, now=d1)
    await limiter.acquire(Category.DATA, now=d2)
    assert limiter.used_today(Category.DATA, d2) == 1
