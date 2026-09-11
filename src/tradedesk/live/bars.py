"""15-minute bars from ticks, anchored to the 09:15 session open (doc: intraday buckets
are aligned to 09:15, so 09:15, 09:30, 09:45 ...). A bar closes when the first tick of
the next slot arrives or when `flush(now)` is called after the slot ended."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from tradedesk.broker.indstocks.models import IST
from tradedesk.live.models import IntradayBar


def slot_start(ts: datetime, bar_minutes: int = 15, session_open: time = time(9, 15)) -> datetime:
    ts = ts.astimezone(IST)
    anchor = ts.replace(hour=session_open.hour, minute=session_open.minute, second=0, microsecond=0)
    if ts < anchor:
        return anchor - timedelta(minutes=bar_minutes)
    k = int((ts - anchor).total_seconds() // (bar_minutes * 60))
    return anchor + timedelta(minutes=bar_minutes * k)


@dataclass
class _Open:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume_start: int | None
    volume_last: int | None
    ticks: int


@dataclass
class BarBuilder:
    bar_minutes: int = 15
    session_open: time = time(9, 15)
    _open: dict[str, _Open] = field(default_factory=dict)
    day_high: dict[str, float] = field(default_factory=dict)
    day_low: dict[str, float] = field(default_factory=dict)
    day_open: dict[str, float] = field(default_factory=dict)
    last_price: dict[str, float] = field(default_factory=dict)

    def on_tick(
        self, code: str, ts: datetime, price: float, day_volume: int | None = None
    ) -> IntradayBar | None:
        """Record a tick; returns the bar that just closed, if this tick opened a new slot."""
        closed: IntradayBar | None = None
        start = slot_start(ts, self.bar_minutes, self.session_open)
        cur = self._open.get(code)
        if cur is not None and cur.start != start:
            closed = self._finish(code, cur)
            cur = None
        if cur is None:
            self._open[code] = _Open(
                start=start, open=price, high=price, low=price, close=price,
                volume_start=day_volume, volume_last=day_volume, ticks=1,
            )  # fmt: skip
        else:
            cur.high = max(cur.high, price)
            cur.low = min(cur.low, price)
            cur.close = price
            cur.ticks += 1
            if day_volume is not None:
                cur.volume_last = day_volume
                if cur.volume_start is None:
                    cur.volume_start = day_volume
        self.day_open.setdefault(code, price)
        self.day_high[code] = max(self.day_high.get(code, price), price)
        self.day_low[code] = min(self.day_low.get(code, price), price)
        self.last_price[code] = price
        return closed

    def flush(self, now: datetime) -> list[IntradayBar]:
        """Close every bar whose slot ended at or before `now` (no tick arrived to close it)."""
        out: list[IntradayBar] = []
        now = now.astimezone(IST)
        for code, cur in list(self._open.items()):
            if cur.start + timedelta(minutes=self.bar_minutes) <= now:
                out.append(self._finish(code, cur))
                del self._open[code]
        return out

    def _finish(self, code: str, cur: _Open) -> IntradayBar:
        vol = 0
        if cur.volume_start is not None and cur.volume_last is not None:
            vol = max(0, cur.volume_last - cur.volume_start)
        return IntradayBar(
            scrip_code=code,
            start=cur.start,
            end=cur.start + timedelta(minutes=self.bar_minutes),
            open=cur.open,
            high=cur.high,
            low=cur.low,
            close=cur.close,
            volume=vol,
            ticks=cur.ticks,
        )

    def reset_day(self) -> None:
        self._open.clear()
        self.day_high.clear()
        self.day_low.clear()
        self.day_open.clear()
        self.last_price.clear()
