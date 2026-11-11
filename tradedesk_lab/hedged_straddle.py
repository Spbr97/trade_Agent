"""The 9:20 straddle with protection bought: does a capped-loss version keep enough of the income?

The two paper straddles (option_income.py) have the agent's best figures so far, but one lot needs
an estimated Rs 2.3-3.6 lakh of margin, more than the owner's Rs 1 lakh, and a session's loss has no
ceiling. Buying a call above and a put below the sold strike (an iron butterfly) fixes both: the
most a session can lose is known at the entry, and the margin of a capped position is far lower.
The price is the premium paid for the two bought options and four more orders a day. Owner approved
this replay on 2026-11-11. Research only: it reads saved quotes and never places an order.

DATA. option_income_watch.py has saved every minute's Nifty option chain since 2026-10-19 (bid, ask
and last for about 20 strikes either side of the money). A replay over those minutes fills exactly
as the live paper straddles fill: sell at the bid, buy at the ask, one lot.

RULE. At the first saved minute from 09:20, for the nearest expiry: sell the at-the-money call and
put at the bid (the live straddle's own entry) and buy the call 300 points above and the put 300
points below at the ask. Three ways out, each its own paper book:
  hedged_30 / hedged_40  each sold option has the live stop, bought back at the ask once the ask
                         reaches entry + 30% (or 40%); its bought partner is sold at the bid the
                         same minute. Whatever is open is closed the same way at 15:15.
  hedged_hold            no stops: the bought options are the protection. All four are closed at
                         15:15.
A bought option with no bid is left to expire and counts as zero, with no sell order. A session on
which the Nifty does not move in the first polls is a holiday, as in the live watch. Charges per
order from config/option_costs.yaml, brokerage still provisional. The cap of a session is (300 -
net premium received) x the lot: the most hedged_hold can lose before charges.
Reported beside it, not judged: the same rule with 200- and 500-point protection, and the two
unhedged straddles replayed from the same minutes, which must reproduce the live ledger.

WHAT THE REPLAY CAN DECIDE (written 2026-11-11, before any hedged figure was computed). The
saved sessions are few and are known to have been good for the unhedged straddle, so a replay
cannot prove a strategy. It only decides whether a forward paper trial is worth running, which
needs all of:
  a. all four options quoted at the entry minute on at least 6 in 7 of the saved sessions;
  b. for at least one of the three, total profit above total loss after charges;
  c. hedged_hold never losing more than its session's cap plus charges.
FORWARD BAR, fixed now for the trial if the owner approves it (sessions from the trial's start
only; the replayed ones stay a separate line). Each of the three alone, at 60 sessions:
  1. total profit at least 1.5 times total loss after charges;
  2. no session lost more than its cap plus charges;
  3. its last 20 sessions sum to a profit (the money board's pause rule);
  4. ahead of holding the Nifty over the same days on the capital one lot really needs, once the
     owner has read that margin from the broker (the API's calculator prices one order, not four).
A pass is a proposal to the owner, never an order. No width, stop or bar is changed after a run.

RESULT OF THE REPLAY (2026-11-11; 14 finished sessions, 2026-10-19 to 2026-11-09, one lot, after
charges). The replayed unhedged straddles match the live ledger to the paisa on all 28 rows, so the
replay fills as the live watch does. WORTH A FORWARD TRIAL by the three-part rule: protection was
quoted on every session, two versions' profit beats their loss, hedged_hold stayed inside its cap.
  hedged_30    10 won, +Rs 12,678 (profit Rs 21,388, loss Rs 8,710), worst session -Rs 3,935
  hedged_40     9 won, +Rs 10,574 (profit Rs 14,263, loss Rs 3,689), worst session -Rs 1,525
  hedged_hold   7 won, -Rs 14,093 (profit Rs 5,747, loss Rs 19,840), worst session -Rs 9,181
  straddle_30  11 won, +Rs 24,659 (profit Rs 34,501, loss Rs 9,842), worst session -Rs 5,044
  straddle_40  12 won, +Rs 24,383 (profit Rs 26,140, loss Rs 1,757), worst session -Rs 1,580
Protection costs about half the income: the two bought options, their bid-ask spreads and four
more orders (charges about Rs 234 a session). The mean premium kept is 196 points and the mean cap
Rs 6,775. On these sessions the stops, not the protection, did the limiting: the worst sessions
barely change, and without stops the hedged position loses money. What the protection buys is
what 14 quiet sessions cannot show: a known worst case on a gap day and a far smaller margin.
Other widths, reported only: 200 points +Rs 9,411 / +Rs 7,700 / -Rs 11,473 (cap Rs 3,209);
500 points +Rs 16,754 / +Rs 15,053 / -Rs 15,969 (cap Rs 16,406).

    python -m tradedesk_lab.hedged_straddle
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tradedesk_lab import option_income as oi
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "hedged_straddle"
CHAINS = oi.ROOT / "chain"
WING = 300
OTHER_WINGS = (200, 500)
VARIANTS: dict[str, float | None] = {"hedged_30": 0.30, "hedged_40": 0.40, "hedged_hold": None}
ENTRY_FROM, SQUARE_OFF = "09:20", "15:15"
MIN_QUOTED = 6 / 7
COSTS_FILE = Path(__file__).resolve().parents[1] / "config" / "option_costs.yaml"


def expand(minute: Mapping[str, Any]) -> dict[str, Any]:
    """A saved minute (option_income.compact) back in the live chain's shape, so the live
    module's own quote and strike functions price the replay."""

    strikes = {
        k: {
            side: {"top_bid_price": v[0], "top_ask_price": v[1], "last_price": v[2],
                   "greeks": {"delta": v[4] if len(v) > 4 else None}}
            for side, v in legs.items()
        }
        for k, legs in (minute.get("strikes") or {}).items()
    }  # fmt: skip
    return {"underlying_ltp": minute.get("ltp"), "expiry": minute.get("expiry"),
            "strikes": strikes}  # fmt: skip


@dataclass
class Wing:
    side: str  # "ce" | "pe"
    strike: float
    entry: float  # bought at the ask
    exit: float | None = None  # sold at the bid, or 0.0 when left to expire
    exit_at: str | None = None
    reason: str | None = None


@dataclass
class Hedged:
    name: str
    wing: int
    expiry: str
    opened_at: str
    shorts: list[oi.Leg] = field(default_factory=list)
    wings: list[Wing] = field(default_factory=list)
    worst_open_inr: float = 0.0

    def step(self, chain: Mapping[str, Any], now: str, *, square_off: bool) -> None:
        for short, wing in zip(self.shorts, self.wings, strict=True):
            if short.exit is None:
                q = oi.quote(chain, short.strike, short.side)
                if q and q["ask"] > 0:
                    if q["ask"] >= short.stop:
                        short.exit, short.exit_at, short.reason = q["ask"], now, "stop"
                    elif square_off:
                        short.exit, short.exit_at, short.reason = q["ask"], now, SQUARE_OFF
            if short.exit is not None and wing.exit is None:
                w = oi.quote(chain, wing.strike, wing.side)
                if w is not None:
                    wing.exit, wing.exit_at, wing.reason = max(w["bid"], 0.0), now, short.reason
        mark = self.open_value(chain)
        if mark is not None:
            self.worst_open_inr = min(self.worst_open_inr, mark)

    def open_value(self, chain: Mapping[str, Any]) -> float | None:
        """Gross rupees now: sold options bought back at the ask, bought ones sold at the bid."""

        total = 0.0
        for short, wing in zip(self.shorts, self.wings, strict=True):
            owed, worth = short.exit, wing.exit
            if owed is None:
                q = oi.quote(chain, short.strike, short.side)
                if not q or q["ask"] <= 0:
                    return None
                owed = q["ask"]
            if worth is None:
                w = oi.quote(chain, wing.strike, wing.side)
                if w is None:
                    return None
                worth = max(w["bid"], 0.0)
            total += (short.entry - owed + worth - wing.entry) * oi.LOT
        return total

    @property
    def done(self) -> bool:
        return all(x.exit is not None for x in (*self.shorts, *self.wings))

    @property
    def credit(self) -> float:
        """Premium points received at the entry, after paying for the protection."""

        return sum(s.entry for s in self.shorts) - sum(w.entry for w in self.wings)

    def result(self, costs: oi.OptionCosts) -> dict[str, Any]:
        gross = sum((s.entry - (s.exit or 0.0)) * oi.LOT for s in self.shorts)
        gross += sum(((w.exit or 0.0) - w.entry) * oi.LOT for w in self.wings)
        fees = sum(costs.trade(s.entry, oi.LOT, "sell") + costs.trade(s.exit or 0.0, oi.LOT, "buy")
                   for s in self.shorts)  # fmt: skip
        fees += sum(costs.trade(w.entry, oi.LOT, "buy") for w in self.wings)
        fees += sum(costs.trade(w.exit, oi.LOT, "sell") for w in self.wings if w.exit)
        return {
            "rule": self.name, "wing": self.wing, "expiry": self.expiry,
            "opened_at": self.opened_at, "credit_points": round(self.credit, 2),
            "cap_inr": round((self.wing - self.credit) * oi.LOT, 2),
            "gross_inr": round(gross, 2), "costs_inr": round(fees, 2),
            "net_inr": round(gross - fees, 2), "worst_open_inr": round(self.worst_open_inr, 2),
            "shorts": [{**asdict(s), "stop": None if math.isinf(s.stop) else s.stop}
                       for s in self.shorts],
            "wings": [asdict(w) for w in self.wings],
        }  # fmt: skip


def open_hedged(chain: Mapping[str, Any], now: str, wing: int) -> list[Hedged]:
    """The three books at this width, or none if any of the four options has no usable quote."""

    strike = oi.atm_strike(chain)
    ce, pe = oi.quote(chain, strike, "ce"), oi.quote(chain, strike, "pe")
    up, down = oi.quote(chain, strike + wing, "ce"), oi.quote(chain, strike - wing, "pe")
    if not ce or not pe or ce["bid"] <= 0 or pe["bid"] <= 0:
        return []
    if not up or not down or up["ask"] <= 0 or down["ask"] <= 0:
        return []
    out = []
    for name, pct in VARIANTS.items():
        stop = (1 + pct) if pct is not None else math.inf
        out.append(Hedged(
            name, wing, str(chain.get("expiry")), now,
            [oi.Leg("ce", strike, ce["bid"], ce["bid"] * stop),
             oi.Leg("pe", strike, pe["bid"], pe["bid"] * stop)],
            [Wing("ce", strike + wing, up["ask"]), Wing("pe", strike - wing, down["ask"])],
        ))  # fmt: skip
    return out


def replay_day(minutes: Sequence[Mapping[str, Any]], costs: oi.OptionCosts) -> (
    dict[str, Any] | None
):  # fmt: skip
    """One saved session: every hedged book at every width and the two unhedged straddles.
    None on a holiday or a day with no minute from 09:20."""

    if not minutes:
        return None
    nearest = min(str(m.get("expiry")) for m in minutes)
    main = [m for m in minutes if str(m.get("expiry")) == nearest]
    early = [m.get("ltp") for m in main if str(m["t"])[11:16] < "09:21"]
    if len(early) >= 3 and len(set(early)) <= 1:
        return None
    hedged: list[Hedged] = []
    plain: list[oi.Straddle] = []
    opened = False
    for m in main:
        now, hhmm = str(m["t"]), str(m["t"])[11:16]
        chain = expand(m)
        if not chain["strikes"] or chain["underlying_ltp"] is None:
            continue
        if not opened and hhmm >= ENTRY_FROM:
            opened = True
            plain = oi.open_straddles(chain, now)
            for width in (WING, *OTHER_WINGS):
                hedged += open_hedged(chain, now, width)
        for book in (*plain, *hedged):
            if not book.done:
                book.step(chain, now, square_off=hhmm >= SQUARE_OFF)
    if not opened:
        return None
    return {
        "day": str(main[0]["t"])[:10], "expiry": nearest,
        "hedged": [h.result(costs) for h in hedged if h.done],
        "unfinished": [f"{h.name}/{h.wing}" for h in hedged if not h.done],
        "plain": [s.result(costs) for s in plain if s.done],
        "quoted": {w: any(h.wing == w for h in hedged) for w in (WING, *OTHER_WINGS)},
    }  # fmt: skip


def totals(nets: Sequence[float]) -> dict[str, Any]:
    """The owner's figures: rupees made, total profit against total loss, the worst session."""

    return {
        "sessions": len(nets), "won": sum(1 for n in nets if n > 0),
        "net_inr": round(sum(nets), 2), "total_profit": round(sum(n for n in nets if n > 0), 2),
        "total_loss": round(sum(n for n in nets if n < 0), 2),
        "worst_session": round(min(nets), 2) if nets else None,
    }  # fmt: skip


def summarise(days: Sequence[Mapping[str, Any]], ledger: Sequence[Mapping[str, Any]]) -> (
    dict[str, Any]
):  # fmt: skip
    """Every book's totals, the ledger check, and the three-part verdict of the docstring."""

    books: dict[str, dict[str, Any]] = {}
    over_cap = []
    for width in (WING, *OTHER_WINGS):
        for name in VARIANTS:
            rows = [r for d in days for r in d["hedged"]
                    if r["rule"] == name and r["wing"] == width]  # fmt: skip
            key = name if width == WING else f"{name}/{width}"
            books[key] = {
                **totals([r["net_inr"] for r in rows]),
                "mean_credit_points": _mean([r["credit_points"] for r in rows]),
                "mean_cap_inr": _mean([r["cap_inr"] for r in rows]),
                "mean_costs_inr": _mean([r["costs_inr"] for r in rows]),
                "worst_open_inr": min((r["worst_open_inr"] for r in rows), default=None),
            }  # fmt: skip
            if name == "hedged_hold" and width == WING:
                over_cap = [r["opened_at"][:10] for r in rows
                            if -r["net_inr"] > r["cap_inr"] + r["costs_inr"] + 0.01]  # fmt: skip
    recorded = {(str(r.get("closed_on")), r.get("rule")): float(r["net_inr"])
                for r in ledger if r.get("rule") in oi.STOPS and "net_inr" in r}  # fmt: skip
    gaps = []
    for name in oi.STOPS:
        rows = [(d["day"], r) for d in days for r in d["plain"] if r["rule"] == name]
        books[f"replayed {name}"] = totals([r["net_inr"] for _, r in rows])
        gaps += [abs(r["net_inr"] - recorded[(day, name)]) for day, r in rows
                 if (day, name) in recorded]  # fmt: skip
    quoted = sum(1 for d in days if d["quoted"][WING])
    fails = []
    if not days or quoted / len(days) < MIN_QUOTED:
        fails.append(f"quoted on only {quoted} of {len(days)} sessions")
    if not any(books[n]["total_profit"] > -books[n]["total_loss"] for n in VARIANTS):
        fails.append("no version's profit beats its loss")
    if over_cap:
        fails.append(f"hedged_hold lost more than its cap on {over_cap}")
    return {
        "sessions": len(days), "quoted_sessions": quoted, "books": books,
        "ledger_check": {"sessions_compared": len(gaps),
                         "largest_gap_inr": round(max(gaps), 2) if gaps else None},
        "worth_a_forward_trial": not fails, "fails": fails,
    }  # fmt: skip


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def load_costs() -> oi.OptionCosts:
    import yaml

    raw = yaml.safe_load(COSTS_FILE.read_text(encoding="utf-8")) if COSTS_FILE.exists() else {}
    return oi.load_costs((raw or {}).get("option_costs"))


def run() -> dict[str, Any]:
    costs = load_costs()
    days = []
    for path in sorted(CHAINS.glob("*.jsonl")):
        day = replay_day(oi._minutes(CHAINS, path.stem), costs)
        if day is not None:
            days.append(day)
    ledger = [json.loads(x) for x in oi.LEDGER.read_text(encoding="utf-8").splitlines()
              if x.strip()] if oi.LEDGER.exists() else []  # fmt: skip
    report = {**summarise(days, ledger), "days": days}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "replay.json").write_text(json.dumps(report, indent=1, default=str), "utf-8")
    return report


def main() -> None:
    r = run()
    print(f"{r['sessions']} sessions replayed, protection quoted at the entry on "
          f"{r['quoted_sessions']}; ledger check {r['ledger_check']}")  # fmt: skip
    for name, b in r["books"].items():
        extra = ""
        if "mean_cap_inr" in b:
            extra = (f", credit {b['mean_credit_points']} pts, cap Rs {b['mean_cap_inr']}, "
                     f"charges Rs {b['mean_costs_inr']} a session, worst open "
                     f"Rs {b['worst_open_inr']}")  # fmt: skip
        print(f"{name}: {b['sessions']} sessions, {b['won']} won, Rs {b['net_inr']:+,.0f} "
              f"(profit Rs {b['total_profit']:,.0f}, loss Rs {b['total_loss']:,.0f}), worst "
              f"session Rs {b['worst_session']}{extra}")  # fmt: skip
    for d in r["days"]:
        row = {x["rule"]: round(x["net_inr"]) for x in d["hedged"] if x["wing"] == WING}
        row.update({x["rule"]: round(x["net_inr"]) for x in d["plain"]})
        print(" ", d["day"], d["expiry"], row, d["unfinished"] or "")
    print("worth a forward trial:", r["worth_a_forward_trial"], r["fails"])


if __name__ == "__main__":
    main()
