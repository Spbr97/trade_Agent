"""Client-side rate limiting for INDstocks (docs/indstocks-api.md, "Rate Limiting").

Every REST call goes through `RateLimiter.acquire(category)`. Per-second limits use a
leaky bucket; the daily cap is counted in IST and *fails closed* — once 100,000 calls are
spent, further calls raise instead of risking a lockout.

| Category      | per second | per day |
| data          | 5          | 100,000 |  instruments, historical, option chain
| quote         | 5          | 100,000 |
| non_trading   | 15         | 100,000 |  profile, funds, order history
| token         | 1 / 60 s   | -       |  handled in auth.py (single call, throttled there)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from aiolimiter import AsyncLimiter

from tradedesk.broker.indstocks.models import IST


class Category(StrEnum):
    DATA = "data"
    QUOTE = "quote"
    NON_TRADING = "non_trading"


# (max calls, per seconds). Kept slightly under the documented ceiling for clock jitter.
_PER_SECOND: dict[Category, tuple[int, float]] = {
    Category.DATA: (5, 1.0),
    Category.QUOTE: (5, 1.0),
    Category.NON_TRADING: (15, 1.0),
}
DAILY_CAP = 100_000


class DailyLimitExceeded(RuntimeError):
    """Raised when the documented 100,000/day budget for a category is spent."""


@dataclass
class _DayCounter:
    day: date
    count: int = 0


@dataclass
class RateLimiter:
    daily_cap: int = DAILY_CAP
    _buckets: dict[Category, AsyncLimiter] = field(default_factory=dict, init=False)
    _days: dict[Category, _DayCounter] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def _bucket(self, category: Category) -> AsyncLimiter:
        if category not in self._buckets:
            rate, period = _PER_SECOND[category]
            self._buckets[category] = AsyncLimiter(rate, period)
        return self._buckets[category]

    def used_today(self, category: Category, now: datetime | None = None) -> int:
        today = (now or datetime.now(IST)).date()
        c = self._days.get(category)
        return c.count if c and c.day == today else 0

    async def acquire(self, category: Category, now: datetime | None = None) -> None:
        """Block until a call in `category` is allowed; raise if the daily budget is spent."""
        today = (now or datetime.now(IST)).date()
        async with self._lock:
            counter = self._days.get(category)
            if counter is None or counter.day != today:
                counter = _DayCounter(day=today)
                self._days[category] = counter
            if counter.count >= self.daily_cap:
                raise DailyLimitExceeded(
                    f"{category}: {counter.count} calls today >= cap {self.daily_cap}"
                )
            counter.count += 1
        await self._bucket(category).acquire()
