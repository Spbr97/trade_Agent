"""Position watch during market hours (PLAN.md 8): stop hit / near stop, T1 reached,
15:15 close check, time-stop countdown. Pure functions over a Position and a price; the
monitor de-duplicates alerts per session."""

from __future__ import annotations

from datetime import datetime

from tradedesk.backtest.fills import Position
from tradedesk.live.models import Alert, AlertKind, AlertLevel, SessionRules


def watch_price(
    pos: Position, price: float, atr: float, now: datetime, rules: SessionRules
) -> list[Alert]:
    s = pos.signal
    out: list[Alert] = []
    if price <= pos.stop:
        out.append(
            Alert(
                kind=AlertKind.STOP_HIT,
                level=AlertLevel.URGENT,
                at=now,
                scrip_code=s.scrip_code,
                symbol=s.symbol,
                message=(
                    f"{s.symbol}: {price:.2f} at/below stop {pos.stop:.2f} - exit {pos.qty_open}"
                ),
                payload={"price": price, "stop": pos.stop, "qty": pos.qty_open},
            )
        )
    elif atr > 0 and price <= pos.stop + rules.near_stop_atr * atr:
        out.append(
            Alert(
                kind=AlertKind.NEAR_STOP,
                level=AlertLevel.WARNING,
                at=now,
                scrip_code=s.scrip_code,
                symbol=s.symbol,
                message=(
                    f"{s.symbol}: {price:.2f} within {rules.near_stop_atr:g} ATR "
                    f"of stop {pos.stop:.2f}"
                ),
                payload={"price": price, "stop": pos.stop},
            )
        )
    if not pos.partial_done and price >= s.t1:
        half = max(1, round(pos.qty_initial * s.exit_plan.partial_fraction))
        out.append(
            Alert(
                kind=AlertKind.T1_REACHED,
                level=AlertLevel.URGENT,
                at=now,
                scrip_code=s.scrip_code,
                symbol=s.symbol,
                message=(
                    f"{s.symbol}: T1 {s.t1:.2f} reached - sell {half} of {pos.qty_open}, "
                    f"move stop to breakeven {pos.entry_price:.2f}"
                ),
                payload={"price": price, "t1": s.t1, "sell_qty": half},
            )
        )
    return out


def close_check(pos: Position, price: float, ema10: float | None, now: datetime) -> list[Alert]:
    """15:15: positions below a closing-basis stop (trail under EMA10 after the partial) and
    same-day trades still open."""
    s = pos.signal
    out: list[Alert] = []
    if (
        pos.partial_done
        and s.exit_plan.trail == "ema10_close"
        and ema10 is not None
        and price < ema10
    ):
        out.append(
            Alert(
                kind=AlertKind.CLOSE_CHECK,
                level=AlertLevel.URGENT,
                at=now,
                scrip_code=s.scrip_code,
                symbol=s.symbol,
                message=(
                    f"{s.symbol}: {price:.2f} below the 10-day EMA {ema10:.2f} "
                    "- exit before the close"
                ),
                payload={"price": price, "ema10": ema10, "dedupe": now.date().isoformat()},
            )
        )
    plan = s.exit_plan
    if (
        pos.sessions_held + 1 >= plan.time_stop_sessions
        and pos.unrealised_r(price) < plan.time_stop_min_r
    ):
        out.append(
            Alert(
                kind=AlertKind.TIME_STOP,
                level=AlertLevel.WARNING,
                at=now,
                scrip_code=s.scrip_code,
                symbol=s.symbol,
                message=(
                    f"{s.symbol}: session {pos.sessions_held + 1} and only "
                    f"{pos.unrealised_r(price):+.1f}R - time stop says exit before the close"
                ),
                payload={
                    "price": price,
                    "r": pos.unrealised_r(price),
                    "dedupe": now.date().isoformat(),
                },
            )
        )
    return out
