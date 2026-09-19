"""The one shape `gauntlet.py` needs from a trade, deliberately decoupled from any single
strategy module's own trade type (e.g. `tradedesk_lab.crypto_intraday_research.ResolvedCandidate`)
so the harness stays reusable for a future rule set with its own trade representation - a
caller adapts its own trades into this dataclass once, at the boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessTrade:
    scrip_code: str
    entry_at: str  # ISO timestamp
    exit_at: str  # ISO timestamp
    entry_bar: int  # positional index into the underlying bars array this trade filled on
    window_start: int  # first positional index `run_null_baseline` may draw a random entry from
    window_end: int  # last positional index that trade's resolution was bounded to (inclusive)
    entry: float
    stop: float
    target: float
    gross_r: float
    net_r: float
