"""Record a market-hours session (PLAN.md 11 'replay harness'): every tick, quote snapshot
and REST candle response as JSON lines with a timestamp, so it can be replayed through the
trigger monitor at high speed."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST, Tick


class SessionRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self.count = 0

    def _write(self, kind: str, at: datetime, data: dict[str, Any]) -> None:
        line = {"t": int(at.astimezone(IST).timestamp() * 1000), "kind": kind, "data": data}
        self._fh.write(json.dumps(line, separators=(",", ":")) + "\n")
        self.count += 1

    def tick(self, tick: Tick) -> None:
        self._write(
            "tick",
            tick.timestamp,
            {"instrument": tick.instrument, "mode": tick.mode, "data": tick.data},
        )

    def raw_tick(
        self, code: str, at: datetime, price: float, day_volume: int | None = None
    ) -> None:
        self._write("tick", at, {"instrument": code, "price": price, "day_volume": day_volume})

    def quotes(self, at: datetime, quotes: dict[str, dict[str, Any]]) -> None:
        self._write("quotes", at, quotes)

    def event(self, at: datetime, name: str, **payload: Any) -> None:
        self._write("event", at, {"name": name, **payload})

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> SessionRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
