"""09:15-09:30 gap check (PLAN.md 2, 8): armed setups that opened too far past their
trigger are CHASED; held positions that opened below their stop get the gap alert."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from tradedesk.backtest.fills import Position
from tradedesk.engine.lifecycle import SignalState, TrackedSignal
from tradedesk.live.confirmation import is_chased
from tradedesk.live.models import Alert, AlertKind, AlertLevel


def gap_check_signals(
    signals: Sequence[TrackedSignal], opens: Mapping[str, float], now: datetime
) -> list[Alert]:
    out: list[Alert] = []
    for ts in signals:
        if ts.state is not SignalState.ARMED:
            continue
        sig = ts.signal
        o = opens.get(sig.scrip_code)
        if o is None:
            continue
        if is_chased(sig, o):
            ts.move(SignalState.CHASED, now.date(), f"open {o:.2f} > trigger + ATR")
            out.append(
                Alert(
                    kind=AlertKind.CHASED,
                    level=AlertLevel.INFO,
                    at=now,
                    scrip_code=sig.scrip_code,
                    symbol=sig.symbol,
                    message=(
                        f"{sig.symbol}: opened {o:.2f}, more than 1 ATR past the trigger "
                        f"{sig.trigger:.2f} - chased, skip"
                    ),
                    payload={"open": o, "trigger": sig.trigger, "atr": sig.atr},
                )
            )
    return out


def gap_check_positions(
    positions: Sequence[Position], opens: Mapping[str, float], now: datetime
) -> list[Alert]:
    out: list[Alert] = []
    for pos in positions:
        code = pos.signal.scrip_code
        o = opens.get(code)
        if o is None or o > pos.stop:
            continue
        gap_pct = (o / pos.entry_price - 1) * 100
        beyond = (pos.stop - o) * pos.qty_open
        out.append(
            Alert(
                kind=AlertKind.GAP_BELOW_STOP,
                level=AlertLevel.URGENT,
                at=now,
                scrip_code=code,
                symbol=pos.signal.symbol,
                message=(
                    f"{pos.signal.symbol}: opened {o:.2f}, below stop {pos.stop:.2f} "
                    f"({gap_pct:+.1f}% from entry). Rule: exit in the first 15 minutes. "
                    f"Extra loss beyond the stop: Rs {beyond:,.0f}"
                ),
                payload={
                    "open": o,
                    "stop": pos.stop,
                    "qty": pos.qty_open,
                    "dedupe": now.date().isoformat(),
                },
            )
        )
    return out
