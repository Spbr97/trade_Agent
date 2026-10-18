"""Earnings-drift calls: buy a liquid stock that jumped on its results, hold 20 sessions.

Owner-approved 2026-10-18 after tradedesk_lab/edge_hunt.py found the only edge in this
project to clear a pre-registered bar after costs: over 2016-2026, liquid NSE stocks
(20-session turnover >= Rs 20 crore) that rose >= 5% more than the equal-weight market from
the last close before their results date to the first close after it, on >= 2x their usual
volume, beat the market by +1.6% over the next 20 sessions (1,520 calls, z = 4.9, positive
in 2025 and 2026), net of the delivery round trip. Roughly half the calls win; the edge is
in the average. There is no stop in the tested rule, so each call is a fixed Rs 10,000.

Each evening after the NSE close (hooked to tradedesk-after-close): issue calls whose
reaction closed today (buy at the next open), record entries at the real open, close calls at
the close of their 20th session, and say which exit tomorrow. The ledger
(data/reports/earnings_drift_calls.jsonl) is append-only. Live results are judged with the
profit gate's time-uniform range on excess return: if even the most favourable plausible
live average is below zero (80% range, >= 20 closed calls), new calls stop - fail closed.

Advisory only: nothing here places, modifies or cancels an order.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST

DB = Path("data/tradedesk.duckdb")
LEDGER = Path("data/reports/earnings_drift_calls.jsonl")
MIN_JUMP = 0.05
MIN_VOLUME_X = 2.0
MIN_TURNOVER = 2e8  # Rs 20 crore, 20-session average
MARKET_MIN_TURNOVER = 5e7  # the equal-weight market the jump is measured against
MIN_PRICE = 50.0
HOLD_SESSIONS = 20
AMOUNT_INR = 10_000.0
LOOKBACK_DAYS = 90
STOP_MIN_CLOSED = 20
HISTORY = {"calls": 1520, "mean_excess": 0.0160, "mean_net": 0.0194, "years": "2016-2026"}


@dataclass(frozen=True)
class Panels:
    open: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    symbols: dict[str, str]  # trading symbol -> scrip code
    events: pd.DataFrame  # symbol, event_date

    @property
    def sessions(self) -> list[date]:
        return list(self.close.index)


def load(db: Path = DB, *, now: datetime | None = None, tries: int = 40) -> Panels:
    import duckdb

    now = now or datetime.now(IST)
    since = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
    for attempt in range(tries):
        try:
            con = duckdb.connect(str(db), read_only=True)
            break
        except duckdb.IOException:
            if attempt == tries - 1:
                raise
            time.sleep(15)
    try:
        frame = con.execute(
            "SELECT c.scrip_code, c.ts, c.open, c.close, c.volume FROM candles c "
            "JOIN instruments i ON i.scrip_code = c.scrip_code "
            "WHERE c.interval = '1day' AND i.series = 'EQ' AND c.ts >= ?",
            [since],
        ).df()
        symbols = dict(con.execute(
            "SELECT trading_symbol, scrip_code FROM instruments WHERE exch = 'NSE' AND series = 'EQ'"  # noqa: E501
        ).fetchall())  # fmt: skip
        events = con.execute(
            "SELECT DISTINCT symbol, event_date FROM results_events WHERE event_date >= ?",
            [(now - timedelta(days=LOOKBACK_DAYS)).date()],
        ).df()
    finally:
        con.close()
    frame["day"] = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    panels = {
        k: frame.pivot_table(
            index="day", columns="scrip_code", values=k, aggfunc="last"
        ).sort_index()
        for k in ("open", "close", "volume")
    }
    return Panels(panels["open"], panels["close"], panels["volume"], symbols, events)


def _market(p: Panels) -> pd.Series:
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    ok = (turnover >= MARKET_MIN_TURNOVER) & (p.close.shift(1) >= MIN_PRICE)
    return p.close.pct_change(fill_method=None).where(ok).mean(axis=1)


def candidates(p: Panels) -> list[dict[str, Any]]:
    """Results reactions that closed on the LAST stored session and meet the rule."""

    sessions = p.sessions
    if len(sessions) < 25:
        return []
    last = len(sessions) - 1
    turnover20 = (p.close * p.volume).rolling(20).mean().shift(1)
    vol20 = p.volume.rolling(20).mean().shift(1)
    market = _market(p)
    out = []
    seen = set()
    for sym, event_day in p.events.itertuples(index=False):
        code = p.symbols.get(sym)
        if code not in p.close.columns or code in seen:
            continue
        d = pd.Timestamp(event_day).date()
        i1 = next((i for i, s in enumerate(sessions) if s > d), None)
        i0 = max((i for i, s in enumerate(sessions) if s < d), default=None)
        if i1 != last or i0 is None or i0 < 20:
            continue
        c0, c1 = (
            p.close.iat[i0, p.close.columns.get_loc(code)],
            p.close.iat[i1, p.close.columns.get_loc(code)],
        )
        window_vol = p.volume.iloc[i0 + 1 : i1 + 1][code].max()
        usual = vol20.at[sessions[i1], code]
        turnover = turnover20.at[sessions[i1], code]
        if (
            not all(map(math.isfinite, (c0, c1, window_vol, usual, turnover)))
            or c0 <= 0
            or usual <= 0
        ):
            continue
        mkt = float((1 + market.iloc[i0 + 1 : i1 + 1]).prod() - 1)
        jump = c1 / c0 - 1 - mkt
        if (
            jump >= MIN_JUMP
            and window_vol / usual >= MIN_VOLUME_X
            and turnover >= MIN_TURNOVER
            and c0 >= MIN_PRICE
        ):
            seen.add(code)
            out.append({
                "symbol": sym, "scrip_code": code, "results_date": d.isoformat(),
                "reaction_close_on": sessions[i1].isoformat(), "reference_close": float(c1),
                "jump_vs_market": float(jump), "volume_x": float(window_vol / usual),
                "turnover_cr": float(turnover / 1e7),
            })  # fmt: skip
    return sorted(out, key=lambda c: -c["jump_vs_market"])


def read_ledger(path: Path = LEDGER) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def _append(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, default=str) + "\n")


def positions(ledger: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """call_id -> the call folded with its later entry/exit events."""

    calls: dict[str, dict[str, Any]] = {}
    for row in ledger:
        if row["event"] == "call":
            calls[row["call_id"]] = dict(row)
        elif row["call_id"] in calls:
            calls[row["call_id"]].update({k: v for k, v in row.items() if k not in ("event", "at")})
            calls[row["call_id"]][f"{row['event']}_at"] = row["at"]
    return calls


def _cost(entry: float, exit_price: float, qty: int) -> float:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    cm = EquityCostModel(load_config(Path(".")).risk.costs)
    rt = cm.round_trip_cost(
        trade_type=TradeType.DELIVERY, qty=max(1, qty),
        entry_price=Decimal(str(round(entry, 2))), exit_price=Decimal(str(round(exit_price, 2))),
    )  # fmt: skip
    return float(rt.total)


def advance(p: Panels, ledger: Sequence[Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Entry and exit events now knowable from stored candles (never guessed)."""

    sessions = p.sessions
    events = []
    for cid, c in positions(ledger).items():
        code = c["scrip_code"]
        if code not in p.close.columns:
            continue
        reacted = date.fromisoformat(c["reaction_close_on"])
        after = [i for i, s in enumerate(sessions) if s > reacted]
        if not after:
            continue
        e = after[0]
        if "entry_price" not in c:
            price = float(p.open.iat[e, p.open.columns.get_loc(code)])
            if not math.isfinite(price) or price <= 0:
                continue
            qty = int(AMOUNT_INR // price)
            events.append({"event": "entered", "call_id": cid, "at": now.isoformat(),
                           "entry_on": sessions[e].isoformat(), "entry_price": price, "qty": qty})  # fmt: skip  # noqa: E501
            c = {**c, "entry_price": price, "qty": qty, "entry_on": sessions[e].isoformat()}
        x = e + HOLD_SESSIONS - 1
        if "exit_price" not in c and x < len(sessions):
            exit_price = float(p.close.iat[x, p.close.columns.get_loc(code)])
            market = p.close.iloc[x] / p.open.iloc[e] - 1
            turnover = (p.close * p.volume).rolling(20).mean().shift(1).iloc[e]
            mkt = float(market[(turnover >= MARKET_MIN_TURNOVER) & market.notna()].mean())
            gross = exit_price / c["entry_price"] - 1
            qty = int(c["qty"])
            cost_pct = _cost(c["entry_price"], exit_price, qty) / max(1.0, qty * c["entry_price"])
            events.append({
                "event": "exited", "call_id": cid, "at": now.isoformat(), "exit_on": sessions[x].isoformat(),  # noqa: E501
                "exit_price": exit_price, "net_return": gross - cost_pct, "market_return": mkt,
                "excess_return": gross - mkt, "net_inr": qty * c["entry_price"] * (gross - cost_pct),  # noqa: E501
            })  # fmt: skip
    return events


def verdict(ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from tradedesk.profit_gate import STOP_ALPHA, confidence_sequence

    closed = sorted(
        (c for c in positions(ledger).values() if "excess_return" in c), key=lambda c: c["exit_on"]
    )
    excess = [float(c["excess_return"]) for c in closed]
    lower, upper = confidence_sequence(excess, alpha=STOP_ALPHA)
    stopped = len(excess) >= STOP_MIN_CLOSED and upper < 0
    return {
        "closed": len(excess),
        "winners": sum(1 for c in closed if c["net_return"] > 0),
        "mean_net_return": sum(c["net_return"] for c in closed) / len(closed) if closed else None,
        "mean_excess": sum(excess) / len(excess) if excess else None,
        "net_inr": sum(c["net_inr"] for c in closed),
        "range_80": [lower, upper] if len(excess) >= 20 else None,
        "status": "stopped" if stopped else "live",
    }


def run(
    *, db: Path = DB, ledger_path: Path = LEDGER, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(IST)
    p = load(db, now=now)
    ledger = read_ledger(ledger_path)
    moves = advance(p, ledger, now)
    _append(moves, ledger_path)
    ledger = read_ledger(ledger_path)
    judged = verdict(ledger)
    open_codes = {c["scrip_code"] for c in positions(ledger).values() if "exit_price" not in c}
    issued = []
    if judged["status"] == "live" and p.sessions:
        for c in candidates(p):
            cid = f"{c['symbol']}:{c['results_date']}"
            if cid in positions(ledger) or c["scrip_code"] in open_codes:
                continue
            issued.append({"event": "call", "call_id": cid, "at": now.isoformat(),
                           "amount_inr": AMOUNT_INR, "hold_sessions": HOLD_SESSIONS, **c})  # fmt: skip  # noqa: E501
    _append(issued, ledger_path)
    ledger = read_ledger(ledger_path)
    held = positions(ledger)
    sessions = p.sessions
    exit_tomorrow = []
    for c in held.values():
        if "entry_on" in c and "exit_price" not in c:
            done = sum(1 for s in sessions if s >= date.fromisoformat(c["entry_on"]))
            if done == HOLD_SESSIONS - 1:
                exit_tomorrow.append(c["symbol"])
    return {
        "last_session": sessions[-1].isoformat() if sessions else None,
        "issued": issued,
        "entered": [m for m in moves if m["event"] == "entered"],
        "exited": [m for m in moves if m["event"] == "exited"],
        "exit_tomorrow": sorted(exit_tomorrow),
        "verdict": judged,
    }


def messages(result: Mapping[str, Any]) -> list[tuple[str, str]]:
    out = []
    if result["issued"]:
        lines = []
        for n, c in enumerate(result["issued"], start=1):
            qty = int(AMOUNT_INR // c["reference_close"])
            lines.append(
                f"{n}. {c['symbol']} BUY at next open (last close Rs {c['reference_close']:,.2f}, "
                f"~{qty} sh for Rs {AMOUNT_INR:,.0f}) | exit: close of the {HOLD_SESSIONS}th session | "  # noqa: E501
                f"results +{100 * c['jump_vs_market']:.1f}% vs market on {c['volume_x']:.1f}x volume"  # noqa: E501
            )
        out.append((
            f"EARNINGS DRIFT: {len(result['issued'])} call(s) for the next open",
            "\n".join(lines) + "\nNo stop (fixed 20-session hold). History: beats the market by "
            f"+{100 * HISTORY['mean_excess']:.1f}% on average; about half win. Advisory only.",
        ))  # fmt: skip
    if result["exit_tomorrow"]:
        out.append(
            ("EARNINGS DRIFT: exit tomorrow at the close", ", ".join(result["exit_tomorrow"]))
        )
    if result["exited"]:
        body = "\n".join(
            f"{m['call_id'].split(':')[0]}: net {100 * m['net_return']:+.2f}% (Rs {m['net_inr']:+,.0f}), "  # noqa: E501
            f"vs market {100 * m['excess_return']:+.2f}%"
            for m in result["exited"]
        )
        out.append(("EARNINGS DRIFT: closed today", body))
    if result["verdict"]["status"] == "stopped":
        out.append(
            (
                "EARNINGS DRIFT STOPPED",
                "Live results are below the market after costs; no new calls.",
            )
        )
    return out
