"""Replay a recorded session through a TriggerMonitor at full speed (PLAN.md 11).

Lines are fed in timestamp order; the monitor's bar builder closes bars from the tick
timestamps themselves, and `flush` is called at the end so the last bar closes. Staleness
is evaluated against the recorded clock, so a real outage in the recording reproduces the
DATA STALE alert on replay.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk.live.models import Alert
from tradedesk.live.trigger_monitor import TriggerMonitor


@dataclass
class Recorded:
    at: datetime
    kind: str
    data: dict[str, Any]


def read_session(path: Path) -> Iterator[Recorded]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            yield Recorded(
                at=datetime.fromtimestamp(raw["t"] / 1000, tz=IST),
                kind=str(raw["kind"]),
                data=dict(raw.get("data") or {}),
            )


def _price_of(data: dict[str, Any]) -> float | None:
    if "price" in data:
        return float(data["price"])
    inner = data.get("data") or {}
    if "ltp" in inner:
        return float(inner["ltp"])
    return None


def _code_of(data: dict[str, Any]) -> str:
    inst = str(data.get("instrument", ""))
    # WebSocket ticks carry the bare token ("2885"); recordings made from raw_tick carry the
    # REST code ("NSE_2885"). Normalise to the REST form.
    return inst if "_" in inst else f"NSE_{inst}"


@dataclass
class ReplayResult:
    alerts: list[Alert] = field(default_factory=list)
    ticks: int = 0
    bars: int = 0
    resyncs: int = 0


def replay(
    records: Iterable[Recorded],
    monitor: TriggerMonitor,
    *,
    staleness_every: timedelta = timedelta(seconds=30),
) -> ReplayResult:
    result = ReplayResult()
    last_check: datetime | None = None
    now: datetime | None = None
    for rec in records:
        now = rec.at
        if last_check is None or now - last_check >= staleness_every:
            result.alerts.extend(monitor.check_staleness(now))
            result.alerts.extend(monitor.flush(now))
            last_check = now
        if rec.kind == "tick":
            price = _price_of(rec.data)
            if price is None:
                continue
            vol = rec.data.get("day_volume")
            result.alerts.extend(
                monitor.on_tick(
                    _code_of(rec.data), now, price, int(vol) if vol is not None else None
                )
            )
            result.ticks += 1
        elif rec.kind == "quotes":
            result.alerts.extend(monitor.resync(rec.data, now))
            result.resyncs += 1
        elif rec.kind == "event" and rec.data.get("name") == "close_check":
            result.alerts.extend(monitor.run_close_check(now))
    if now is not None:
        result.alerts.extend(monitor.flush(now + timedelta(minutes=monitor.rules.bar_minutes)))
    result.bars = len(monitor.bars_seen)
    return result
