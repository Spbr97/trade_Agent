"""The owner's real fills, read once a day after the close and matched to our calls.

READ-ONLY: one GET /order-book (a Non-Trading API) through the shared rate limiter; nothing is
ever placed, changed or cancelled. Every executed equity order is recorded once, by order id, in
data/reports/owner_trades.jsonl, so the agent sees which calls the owner actually took and at
what price without anything being logged by hand.

Matching (ISIN, so a call bought on NSE or BSE both count): a BUY within MATCH_DAYS calendar
days of an earnings-drift call being issued is that call's real entry; a later SELL of the same
ISIN closes it. Anything else is kept as "other". The phone hears about matched fills only;
other trades are counted, never itemised - the push service can be read by anyone who knows the
topic name.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

LEDGER = Path("data/reports/owner_trades.jsonl")
INSTRUMENTS = Path("data/instruments/equity.csv")
MATCH_DAYS = 7


def isin_map(path: Path = INSTRUMENTS) -> dict[tuple[str, str], dict[str, str]]:
    """(exchange, security id) -> ISIN and trading symbol, from the stored instrument master."""

    out: dict[tuple[str, str], dict[str, str]] = {}
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("ISIN"):
                    key = (str(row.get("EXCH", "")).upper(), str(row.get("SECURITY_ID", "")))
                    out[key] = {"isin": row["ISIN"], "symbol": row.get("TRADING_SYMBOL", "")}
    except OSError:
        pass
    return out


def executed(
    rows: Iterable[Mapping[str, Any]], isins: Mapping[tuple[str, str], Mapping[str, str]]
) -> list[dict[str, Any]]:
    """Filled equity orders from the raw order book."""

    out = []
    for r in rows:
        if str(r.get("segment") or "").upper() != "EQUITY":
            continue
        try:
            qty = int(float(r.get("traded_qty") or 0))
            price = float(r.get("traded_price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        exch, sid = str(r.get("exchange") or "").upper(), str(r.get("security_id") or "")
        meta = isins.get((exch, sid), {})
        out.append({
            "order_id": str(r.get("id") or r.get("exch_order_id") or ""),
            "at": str(r.get("updated_at") or r.get("created_at") or ""),
            "exchange": exch, "scrip_code": f"{exch}_{sid}",
            "symbol": meta.get("symbol") or str(r.get("name") or ""),
            "isin": str(r.get("isin") or meta.get("isin") or ""),
            "side": str(r.get("txn_type") or "").upper(), "qty": qty, "price": price,
        })  # fmt: skip
    return out


def read(path: Path = LEDGER) -> list[dict[str, Any]]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    except OSError:
        return []


def _day(stamp: str) -> date | None:
    try:
        return datetime.fromisoformat(stamp).date()
    except ValueError:
        return None


def match(
    fills: Sequence[Mapping[str, Any]],
    calls: Mapping[str, Mapping[str, Any]],
    isins: Mapping[tuple[str, str], Mapping[str, str]],
    seen: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """New ledger events for fills not recorded before: owner_entered / owner_exited / other."""

    known = {e.get("order_id") for e in seen}
    holding = {e["call_id"]: e for e in seen if e.get("kind") == "owner_entered"}
    for e in seen:
        if e.get("kind") == "owner_exited":
            holding.pop(e["call_id"], None)
    taken = {e["call_id"] for e in seen if e.get("kind") == "owner_entered"}
    call_isin = {}
    for cid, c in calls.items():
        exch, _, sid = str(c.get("scrip_code", "")).partition("_")
        call_isin[cid] = isins.get((exch.upper(), sid), {}).get("isin", "")
    events = []
    for f in sorted(fills, key=lambda x: x["at"]):
        if not f["order_id"] or f["order_id"] in known or not f["isin"]:
            continue
        known.add(f["order_id"])
        day = _day(f["at"])
        event = {**f, "kind": "other"}
        if f["side"] == "BUY" and day is not None:
            for cid, c in calls.items():
                issued = _day(str(c.get("at", "")))
                if (cid not in taken and call_isin.get(cid) == f["isin"] and issued is not None
                        and 0 <= (day - issued).days <= MATCH_DAYS):  # fmt: skip
                    ref = float(c.get("reference_close") or 0)
                    event = {**f, "kind": "owner_entered", "call_id": cid,
                             "vs_call_close": (f["price"] / ref - 1) if ref else None}  # fmt: skip
                    taken.add(cid)
                    holding[cid] = event
                    break
        elif f["side"] == "SELL":
            for cid, held in list(holding.items()):
                if held.get("isin") == f["isin"]:
                    event = {**f, "kind": "owner_exited", "call_id": cid,
                             "gross_inr": round((f["price"] - held["price"]) * min(
                                 f["qty"], held["qty"]), 2)}  # fmt: skip
                    holding.pop(cid)
                    break
        events.append(event)
    return events


def open_drift_positions(seen: Sequence[Mapping[str, Any]]) -> int:
    held = {e["call_id"] for e in seen if e.get("kind") == "owner_entered"}
    held -= {e["call_id"] for e in seen if e.get("kind") == "owner_exited"}
    return len(held)


def message(events: Sequence[Mapping[str, Any]], open_now: int, slots: int) -> tuple[str, str]:
    lines = []
    for e in events:
        if e["kind"] == "owner_entered":
            vs = e.get("vs_call_close")
            vs_txt = f" ({100 * vs:+.2f}% vs the call's last close)" if vs is not None else ""
            lines.append(f"BUY {e['symbol']} {e['qty']} @ Rs {e['price']:,.2f} on "
                         f"{e['exchange']}: earnings-drift call taken{vs_txt}")  # fmt: skip
        elif e["kind"] == "owner_exited":
            lines.append(f"SELL {e['symbol']} {e['qty']} @ Rs {e['price']:,.2f}: drift call "
                         f"closed, Rs {e['gross_inr']:+,.0f} before charges")  # fmt: skip
    others = sum(1 for e in events if e["kind"] == "other")
    if others:
        lines.append(f"+{others} other trade(s) recorded (not one of our calls)")
    lines.append(f"Earnings-drift positions you hold: {open_now} of {slots} slots")
    return "Your trades today", "\n".join(lines)
