"""Earnings-drift calls: buy a liquid stock that jumped on its results; sell at +10% or day 20.

Owner-approved 2026-10-18 after tradedesk_lab/edge_hunt.py found the only edge in this
project to clear a pre-registered bar after costs: over 2016-2026, liquid NSE stocks
(20-session turnover >= Rs 20 crore) that rose >= 5% more than the equal-weight market from
the last close before their results date to the first close after it, on >= 2x their usual
volume, beat the market over the next 20 sessions (1,520 calls, z = 4.9, positive in 2025
and 2026). Corrected 2026-10-19 (tradedesk_lab/earnings_drift_timing.py): that +1.6% was BEFORE
costs; at the real Rs 10,000 delivery round trip (0.564%) a call returned +1.8% and beat the
equal-weight market by about +0.7% (1,605 calls, 9 of 11 years). Calls made while NIFTY 50 was
above its 200-day average did better (+2.2% net) than those made below it (+0.8% net, still
ahead of the market); each call message says which state the market is in. Roughly half the
calls win; the edge is in the average. No stop in the tested rule: each call is Rs 10,000.
From 2026-10-19 a call is sold when its price first reaches +10% over the entry, or else at the
20th close (tradedesk_lab/takeoff_capture.py): +1.2% net a call, +0.8% over the market, 58% win,
40% reach the target, and ~32% more rupees a year on Rs 1 lakh because slots free sooner.
From 2026-10-24 a call whose reaction closed on a market down day is marked "weaker" (see
DOWN_DAY); it is still issued and kept in the paper record, which reports the two apart.
From 2026-10-28 the mark is narrowed to down days in a FALLING market (NIFTY below its 200-day
average), the only group that lost money (see skip_real_money); with NIFTY above it, down-day
calls were the best group in history and are no longer marked.

Each evening after the NSE close (hooked to tradedesk-after-close): issue calls whose
reaction closed today (buy at the next open), record entries at the real open, close calls at
the +10% target or the close of their 20th session, and say which exit tomorrow. The ledger
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
# Sell when the price first reaches +10% over the entry (tradedesk_lab/takeoff_capture.py,
# 2026-10-19: on Rs 1 lakh in ten slots it earns ~32% more a year than always holding 20
# sessions, because a slot frees sooner; +5% and +7% targets cut too many big winners).
TARGET_PCT = 0.10
HISTORY = {  # the live rule (+10% target or the 20th close) at the real 0.564% cost
    "calls": 1605,
    "mean_excess": 0.0083,
    "mean_net": 0.0119,
    "win_rate": 0.583,
    "hit_target": 0.40,
    "years": "2016-2026",
    "net_above_200dma": 0.0148,
    "net_below_200dma": 0.0043,
    "sd_net": 0.0888,  # spread of one call's result
    "round_trip": 0.00564,
    # By the market's move on the reaction close (exploratory split, 2026-10-24).
    "down_day_calls": 256,
    "net_down_day": 0.0084,
    "excess_down_day": 0.0023,
    "net_other_days": 0.0125,
    "excess_other_days": 0.0094,
    # By market state x down day (tradedesk_lab/drift_market_split.py, 2026-10-28, live exit).
    "falling_down_day_calls": 87,
    "net_falling_down_day": -0.0150,
    "excess_falling_down_day": -0.0210,
    "net_rising_down_day": 0.0224,
    "net_all_but_falling_down_day": 0.0133,
}
# A call whose reaction closed on a market down day - the equal-weight liquid market down 1%
# or more that session - is marked "weaker" (owner-approved 2026-10-24): in history such calls
# kept little of the edge over the market (+0.2% vs +0.9%, positive in 45% of years vs 91%),
# matching tradedesk_lab/down_day_leaders.py (stocks rising while the market falls trail it
# afterwards). A mark only: every call is still issued and kept in the paper record.
# Narrowed 2026-10-28 (owner left it to the agent): only a down day while NIFTY is below its
# 200-day average is marked "skip with real money" - those 87 calls lost -1.5% a call (-2.1%
# vs the market, 1 of 8 years positive); down days in a rising market made +2.2% a call.
DOWN_DAY = -0.01


def skip_real_money(call: Mapping[str, Any]) -> bool:
    """A reaction on a market down day while NIFTY was below its 200-day average."""
    return bool(call.get("down_day")) and call.get("nifty_vs_200dma") == "below"
SCORECARD = Path("data/reports/earnings_drift_scorecard.json")
NIFTY = "NSE_40000001"
NIFTY_LOOKBACK_DAYS = 330  # enough sessions for a 200-day average


@dataclass(frozen=True)
class Panels:
    open: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    symbols: dict[str, str]  # trading symbol -> scrip code
    events: pd.DataFrame  # symbol, event_date
    nifty: pd.Series | None = None  # NIFTY 50 closes by date, ~330 days
    high: pd.DataFrame | None = None  # daily highs, for the +10% target

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
            "SELECT c.scrip_code, c.ts, c.open, c.high, c.close, c.volume FROM candles c "
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
        index = con.execute(
            "SELECT ts, close FROM candles WHERE scrip_code = ? AND interval = '1day' AND ts >= ?",
            [NIFTY, int((now - timedelta(days=NIFTY_LOOKBACK_DAYS)).timestamp())],
        ).df()
    finally:
        con.close()
    frame["day"] = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    panels = {
        k: frame.pivot_table(
            index="day", columns="scrip_code", values=k, aggfunc="last"
        ).sort_index()
        for k in ("open", "high", "close", "volume")
    }
    index["day"] = pd.to_datetime(index["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    nifty = index.set_index("day")["close"].sort_index() if len(index) else None
    return Panels(
        panels["open"], panels["close"], panels["volume"], symbols, events, nifty,
        panels["high"],
    )  # fmt: skip


def market_state(p: Panels, day: date) -> str | None:
    """Whether NIFTY 50 closed "above" or "below" its 200-day average on `day` (None: unknown)."""

    if p.nifty is None:
        return None
    upto = p.nifty[p.nifty.index <= day]
    if len(upto) < 200:
        return None
    return "above" if float(upto.iloc[-1]) > float(upto.iloc[-200:].mean()) else "below"


def _market(p: Panels) -> pd.Series:
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    ok = (turnover >= MARKET_MIN_TURNOVER) & (p.close.shift(1) >= MIN_PRICE)
    return p.close.pct_change(fill_method=None).where(ok).mean(axis=1)


def market_day_move(p: Panels, day: date, market: pd.Series | None = None) -> float | None:
    """The equal-weight liquid market's close-to-close move on `day` (None: unknown)."""

    market = _market(p) if market is None else market
    if day not in market.index:
        return None
    value = float(market.loc[day])
    return value if math.isfinite(value) else None


def annotate(p: Panels, ledger: Sequence[Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """One appended `annotated` event per call recorded before the down-day mark existed."""

    market = _market(p)
    out = []
    for cid, c in positions(ledger).items():
        if "down_day" in c or not c.get("reaction_close_on"):
            continue
        move = market_day_move(p, date.fromisoformat(c["reaction_close_on"]), market)
        if move is not None:
            out.append({"event": "annotated", "call_id": cid, "at": now.isoformat(),
                        "market_day_move": move, "down_day": move <= DOWN_DAY})  # fmt: skip
    return out


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
            move = float(market.iloc[i1])
            move_known = math.isfinite(move)
            out.append({
                "symbol": sym, "scrip_code": code, "results_date": d.isoformat(),
                "reaction_close_on": sessions[i1].isoformat(), "reference_close": float(c1),
                "jump_vs_market": float(jump), "volume_x": float(window_vol / usual),
                "turnover_cr": float(turnover / 1e7),
                "nifty_vs_200dma": market_state(p, sessions[i1]),
                "market_day_move": move if move_known else None,
                "down_day": move_known and move <= DOWN_DAY,
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
        if "exit_price" in c:
            continue
        exit_price, reason = None, f"close of session {HOLD_SESSIONS}"
        if p.high is not None and code in p.high.columns:
            # The +10% target: the first session whose high reaches it sells at the target,
            # or at that session's open if it gapped past (a resting sell order fills there).
            level = c["entry_price"] * (1 + TARGET_PCT)
            for k in range(e, min(x, len(sessions) - 1) + 1):
                high = float(p.high.iat[k, p.high.columns.get_loc(code)])
                if math.isfinite(high) and high >= level:
                    opened = float(p.open.iat[k, p.open.columns.get_loc(code)]) if k > e else 0.0
                    exit_price = max(level, opened if math.isfinite(opened) else 0.0)
                    x, reason = k, f"+{100 * TARGET_PCT:.0f}% target"
                    break
        if exit_price is None and x < len(sessions):
            exit_price = float(p.close.iat[x, p.close.columns.get_loc(code)])
        if exit_price is not None:
            market = p.close.iloc[x] / p.open.iloc[e] - 1
            turnover = (p.close * p.volume).rolling(20).mean().shift(1).iloc[e]
            mkt = float(market[(turnover >= MARKET_MIN_TURNOVER) & market.notna()].mean())
            gross = exit_price / c["entry_price"] - 1
            qty = int(c["qty"])
            cost_pct = _cost(c["entry_price"], exit_price, qty) / max(1.0, qty * c["entry_price"])
            events.append({
                "event": "exited", "call_id": cid, "at": now.isoformat(), "exit_on": sessions[x].isoformat(),  # noqa: E501
                "exit_reason": reason,
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
    _append([*moves, *annotate(p, ledger, now)], ledger_path)
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
    latest = {str(k): float(v) for k, v in p.close.iloc[-1].items()} if len(p.close) else {}
    card = scorecard(ledger, latest, as_of=sessions[-1].isoformat() if sessions else None)
    SCORECARD.parent.mkdir(parents=True, exist_ok=True)
    SCORECARD.write_text(json.dumps(card, indent=1, default=str), encoding="utf-8")
    return {
        "last_session": sessions[-1].isoformat() if sessions else None,
        "issued": issued,
        "entered": [m for m in moves if m["event"] == "entered"],
        "exited": [m for m in moves if m["event"] == "exited"],
        "exit_tomorrow": sorted(exit_tomorrow),
        "verdict": judged,
        "scorecard": card,
    }


def scorecard(
    ledger: Sequence[Mapping[str, Any]], latest_close: Mapping[str, float], *, as_of: str | None
) -> dict[str, Any]:
    """The paper record of every call as if each had been taken with Rs 10,000, next to what
    history says is normal for that many closed calls - so a short run of losses (normal: ten
    calls average a loss about one time in three even with the edge intact) is not mistaken for
    failure, and a lucky run is not mistaken for proof."""

    calls = positions(ledger)
    open_now, closed = [], []
    for c in calls.values():
        if "exit_price" in c:
            closed.append(c)
        elif "entry_price" in c:
            px = latest_close.get(c["scrip_code"])
            if px is not None and math.isfinite(px) and px > 0:
                net = px / float(c["entry_price"]) - 1 - HISTORY["round_trip"]
                inr = net * float(c["entry_price"]) * c["qty"]
                open_now.append(
                    {"symbol": c.get("symbol"), "entry": c["entry_price"], "now": px,
                     "net": net, "inr": inr, "down_day": c.get("down_day"),
                     "nifty_vs_200dma": c.get("nifty_vs_200dma")}
                )  # fmt: skip
    n = len(closed)
    mean = sum(float(c["net_return"]) for c in closed) / n if n else None
    band = None
    status = "no closed calls yet"
    if n:
        half = 1.2816 * HISTORY["sd_net"] / math.sqrt(n)
        band = [HISTORY["mean_net"] - half, HISTORY["mean_net"] + half]
        if n < 10:
            status = "too early to judge"
        elif mean is not None and mean < band[0]:
            status = "worse than history - watch closely"
        elif mean is not None and mean > band[1]:
            status = "better than history"
        else:
            status = "in line with history"
    judged = verdict(ledger)
    lower = (judged.get("range_80") or [None])[0]
    needed = math.ceil((1.2816 * HISTORY["sd_net"] / HISTORY["mean_net"]) ** 2)
    return {
        "as_of": as_of,
        "calls": len(calls),
        "waiting_entry": sum(1 for c in calls.values() if "entry_price" not in c),
        "open": len(open_now),
        "open_mean_net": sum(r["net"] for r in open_now) / len(open_now) if open_now else None,
        "open_inr": sum(r["inr"] for r in open_now),
        "open_positions": open_now,
        "closed": n,
        "closed_won": sum(1 for c in closed if float(c["net_return"]) > 0),
        "closed_mean_net": mean,
        "closed_inr": sum(float(c.get("net_inr") or 0) for c in closed),
        "normal_range_80": band,
        "status": status,
        "proven": bool(lower is not None and lower > 0),
        "closed_calls_for_proof": needed,
        "by_market_day": _by_market_day(open_now, closed),
        "by_nifty_trend": _split(
            open_now, closed, "nifty_vs_200dma",
            (("above_200dma", "above"), ("below_200dma", "below")),
        ),  # fmt: skip
    }


def _by_market_day(
    open_now: Sequence[Mapping[str, Any]], closed: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """The live record split as the history was: calls marked down-day against the rest."""
    return _split(open_now, closed, "down_day", (("down_day", True), ("other_days", False)))


def _split(
    open_now: Sequence[Mapping[str, Any]],
    closed: Sequence[Mapping[str, Any]],
    field: str,
    groups: Sequence[tuple[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Open and closed calls grouped by `field`, so each split the history was measured on
    (down day; NIFTY above or below its 200-day average: +2.2% against +0.8% a call) can be
    checked on live results."""

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    out = {}
    for label, value in groups:
        held = [float(r["net"]) for r in open_now if r.get(field) == value]
        done = [float(c["net_return"]) for c in closed if c.get(field) == value]
        out[label] = {"open": len(held), "open_mean_net": mean(held),
                      "closed": len(done), "closed_mean_net": mean(done)}  # fmt: skip
    return out


def _market_day_part(part: Mapping[str, Any] | None) -> str:
    if not part:
        return "none"
    bits = [
        f"{part[k]} {k} {100 * part[f'{k}_mean_net']:+.1f}%"
        for k in ("open", "closed")
        if part.get(k) and part.get(f"{k}_mean_net") is not None
    ]
    return ", ".join(bits) or "none"


def scorecard_message(card: Mapping[str, Any]) -> tuple[str, str] | None:
    if not card or not card.get("calls"):
        return None
    lines = [
        f"Calls so far: {card['calls']} ({card['waiting_entry']} waiting to enter, "
        f"{card['open']} open, {card['closed']} closed)"
    ]
    if card["open"]:
        lines.append(
            f"Open now: {100 * card['open_mean_net']:+.1f}% on average after costs "
            f"(Rs {card['open_inr']:+,.0f} on Rs {card['open'] * AMOUNT_INR:,.0f})"
        )
    if card["closed"]:
        lines.append(
            f"Closed: {card['closed_won']} of {card['closed']} won, average "
            f"{100 * card['closed_mean_net']:+.1f}% after costs, "
            f"Rs {card['closed_inr']:+,.0f} total"
        )
        low, high = card["normal_range_80"]
        lines.append(
            f"Normal for {card['closed']} closed calls: {100 * low:+.1f}% to {100 * high:+.1f}% "
            f"on average - {card['status']}"
        )
    split = card.get("by_market_day") or {}
    if any(v.get("open") or v.get("closed") for v in split.values()):
        lines.append(
            f"Made on a market down day: {_market_day_part(split.get('down_day'))}; "
            f"other days: {_market_day_part(split.get('other_days'))}"
        )
    trend = card.get("by_nifty_trend") or {}
    if any(v.get("open") or v.get("closed") for v in trend.values()):
        lines.append(
            f"NIFTY above its 200-day average: {_market_day_part(trend.get('above_200dma'))}; "
            f"below: {_market_day_part(trend.get('below_200dma'))}"
        )
    lines.append(
        "PROVEN live: results beat the market" if card["proven"] else
        f"Proof needs about {card['closed_calls_for_proof']} closed calls; losing runs are "
        "normal (10 calls average a loss about 1 time in 3 even when the edge is real)."
    )  # fmt: skip
    return "Earnings-drift paper record (as if Rs 10,000 each)", "\n".join(lines)


def _state_line(calls: Sequence[Mapping[str, Any]]) -> str:
    state = next((c.get("nifty_vs_200dma") for c in calls if c.get("nifty_vs_200dma")), None)
    if state is None:
        return ""
    net = HISTORY[f"net_{state}_200dma"]
    return (
        f" Market: NIFTY {state} its 200-day average; calls made in this state averaged "
        f"{100 * net:+.1f}% after costs."
    )


def _down_day_line() -> str:
    h = HISTORY
    return (
        "FALLING-MARKET DOWN DAY: the results reaction closed on a day the market fell 1% or "
        "more while NIFTY was below its 200-day average. Such calls lost "
        f"{100 * h['net_falling_down_day']:+.1f}% a call after costs "
        f"({100 * h['excess_falling_down_day']:+.1f}% vs the market, "
        f"{h['falling_down_day_calls']} in history; every other call "
        f"{100 * h['net_all_but_falling_down_day']:+.1f}%). With real money, skip them; the "
        "paper record keeps every call."
    )


def _slots_line(held: int | None, slots: int | None) -> str:
    """How many of the owner's Rs 10,000 slots are already in use (src/tradedesk/owner_trades.py
    reads the real fills). Ranking calls by jump size or volume surge did not prove better than
    taking them in order (tradedesk_lab/earnings_extensions.py), so the list order is a default."""

    if held is None or not slots:
        return ""
    return (
        f"\nYour slots: {held} of {slots} in use, {max(0, slots - held)} free. If there are more "
        "calls than free slots, no ranking is proven better - take them in the order listed."
    )


def messages(
    result: Mapping[str, Any], *, held: int | None = None, slots: int | None = None
) -> list[tuple[str, str]]:
    out = []
    if result["issued"]:
        lines = []
        for n, c in enumerate(result["issued"], start=1):
            qty = int(AMOUNT_INR // c["reference_close"])
            lines.append(
                f"{n}. {c['symbol']} BUY at next open (last close Rs {c['reference_close']:,.2f}, "
                f"~{qty} sh for Rs {AMOUNT_INR:,.0f}) | sell at +{100 * TARGET_PCT:.0f}% "
                f"(~Rs {c['reference_close'] * (1 + TARGET_PCT):,.2f}) or the close of the "
                f"{HOLD_SESSIONS}th session | "
                f"results +{100 * c['jump_vs_market']:.1f}% vs market on {c['volume_x']:.1f}x volume"  # noqa: E501
                + (" | FALLING-MARKET DOWN DAY - skip with real money"
                   if skip_real_money(c) else "")  # fmt: skip
            )
        if any(skip_real_money(c) for c in result["issued"]):
            lines.append(_down_day_line())
        out.append((
            f"EARNINGS DRIFT: {len(result['issued'])} call(s) for the next open",
            "\n".join(lines) + f"\nAfter buying, place a sell order (GTT) at {1 + TARGET_PCT:.2f}x "
            "your buy price. No stop. History after costs: "
            f"+{100 * HISTORY['mean_net']:.1f}% a call, +{100 * HISTORY['mean_excess']:.1f}% over "
            f"the market, {100 * HISTORY['win_rate']:.0f}% win, "
            f"{100 * HISTORY['hit_target']:.0f}% reach the target."
            + _state_line(result["issued"]) + " Advisory only." + _slots_line(held, slots),
        ))  # fmt: skip
    if result["exit_tomorrow"]:
        out.append(
            ("EARNINGS DRIFT: exit tomorrow at the close", ", ".join(result["exit_tomorrow"]))
        )
    if result["exited"]:
        body = "\n".join(
            f"{m['call_id'].split(':')[0]}: net {100 * m['net_return']:+.2f}% (Rs {m['net_inr']:+,.0f}), "  # noqa: E501
            f"vs market {100 * m['excess_return']:+.2f}% ({m.get('exit_reason', 'session 20')})"
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
    card = scorecard_message(result.get("scorecard") or {})
    if card:
        out.append(card)
    return out
