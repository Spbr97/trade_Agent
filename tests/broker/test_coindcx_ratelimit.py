"""No daily cap, no categories, unlike INDstocks' - CoinDCX's public endpoints have no
documented limit at all (see ratelimit.py's module docstring); this just proves the
self-imposed per-second ceiling is enforced."""

from __future__ import annotations

import asyncio
import time

from tradedesk.broker.coindcx.ratelimit import RateLimiter


async def test_per_second_limit_is_enforced() -> None:
    limiter = RateLimiter(requests_per_second=3)
    t0 = time.monotonic()
    # 3/s: 7 calls need at least ~2s of wall time (bursts of 3, 3, 1).
    await asyncio.gather(*(limiter.acquire() for _ in range(7)))
    elapsed = time.monotonic() - t0
    assert elapsed >= 1.0
