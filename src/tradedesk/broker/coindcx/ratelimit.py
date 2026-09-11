"""Client-side rate limiting for CoinDCX's public market-data endpoints.

Unlike docs/indstocks-api.md's explicit per-category table, CoinDCX's docs
(docs.coindcx.com, checked 2026-09-12) document rate limits ONLY for the authenticated
SPOT trading API (order placement, cancellation - all out of scope here, no order
placement is ever implemented per CLAUDE.md). The public endpoints used by this adapter
(markets_details, market_data/candles) have NO documented limit at all.

So this is a self-imposed, conservative ceiling, not a transcription of a published
number - unlike broker/indstocks/ratelimit.py, do not treat it as authoritative. 3 req/s
was chosen with no evidence of it being too slow OR too fast; if real usage ever hits a
429 from CoinDCX, tighten this, and if a documented number appears in their docs, use
that instead of this guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aiolimiter import AsyncLimiter

CONSERVATIVE_REQUESTS_PER_SECOND = 3


@dataclass
class RateLimiter:
    requests_per_second: int = CONSERVATIVE_REQUESTS_PER_SECOND
    _bucket: AsyncLimiter = field(init=False)

    def __post_init__(self) -> None:
        self._bucket = AsyncLimiter(self.requests_per_second, 1.0)

    async def acquire(self) -> None:
        await self._bucket.acquire()
