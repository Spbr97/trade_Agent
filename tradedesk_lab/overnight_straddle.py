"""Is the option seller's pay earned overnight? The protected straddle held from the close to the
next morning, replayed on the saved Nifty option minutes.

WHY (2026-11-13; the owner asked for strategies from published work). Bhat, Pandey and Rao, "The
asymmetry in day and night option returns: evidence from an emerging market", Journal of Futures
Markets 44 (2024), on Nifty options 2017-2020: selling options earns a positive, highly significant
return from the close to the next open and a negative, insignificant one from the open to the
close. The premium is pay for carrying the night's gap. The agent's two paper straddles are open
only in the day, the half the paper says does not pay; their 16 good sessions lean on their stops.
The paper reports no result after charges, so this asks the one thing it leaves open: does the
night's premium survive real quotes, eight orders and the cost of protection? Research only: it
reads saved quotes and never places an order.

DATA. option_income_watch.py saves every minute's chain from 09:17 to 15:20 for the nearest expiry,
about 20 strikes either side of the money. Fills as the live paper books fill: sell at the bid, buy
at the ask, one lot, charges per order from config/option_costs.yaml (brokerage provisional).

RULE. At the first saved minute from 15:15, if the nearest expiry is a later day: sell the
at-the-money call and put at the bid and buy the call 300 points above and the put 300 below at
the ask. At the next session's first saved minute from 09:20 buy the two sold options back at the
ask and sell the two bought ones at the bid. Weekends and holidays are one night. No stop: nothing
can be done while the market is shut, so the bought options are the whole protection and the most
a night can lose is (300 - net premium received) x the lot plus charges.
  night_hedged   the rule above, the only book judged.
  night_plain    reported beside it: the same two sold options without the protection. Its loss
                 has no ceiling, so it is a measure of the raw premium, never a candidate.
A night is skipped when any of the four options has no usable quote at the entry, when the next
session is a holiday file, or when that session has no minute from 09:20. If the next morning's
saved strikes no longer include one of the night's options (a gap beyond the saved range),
night_hedged is booked at its full cap and night_plain is left out and counted as unpriced: the
worst nights must not vanish from the record.

WHAT THE REPLAY CAN DECIDE (written before any overnight figure was computed). Fewer than twenty
nights are saved, so the replay cannot prove anything. It decides only whether a forward paper
trial is worth running, which needs all of:
  a. at least 8 nights traded;
  b. night_hedged's total profit above its total loss after charges;
  c. no night lost more than its cap plus charges.
FORWARD BAR, fixed now (nights from the trial's start only; replayed ones stay a separate line).
night_hedged at 60 nights:
  1. total profit at least 1.5 times total loss after charges;
  2. no night lost more than its cap plus charges;
  3. its last 20 nights sum to a profit;
  4. ahead of holding the Nifty over the same days on the capital one lot really needs overnight.
A pass is a proposal to the owner, never an order. No width, time or bar is changed after a run.

    python -m tradedesk_lab.overnight_straddle
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tradedesk_lab import hedged_straddle as hs
from tradedesk_lab import option_income as oi
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "overnight_straddle"
LEDGER = OUT_DIR / "ledger.jsonl"
VERDICT = OUT_DIR / "forward.json"
FROM = date(2026, 11, 13)  # the first entry evening of the forward trial
NIGHTS_NEEDED = 60
PROFIT_TO_LOSS = 1.5
PAUSE_NIGHTS = 20
MIN_NIGHTS = 8
WING = 300
ENTRY_FROM, EXIT_FROM = "15:15", "09:20"
BOOKS = ("night_hedged", "night_plain")


def _chain_at(minutes: Sequence[Mapping[str, Any]], hhmm: str) -> (
    tuple[str, dict[str, Any]] | None
):  # fmt: skip
    """The first usable saved minute at or after `hhmm`, in the live chain's shape."""

    for m in minutes:
        if str(m["t"])[11:16] < hhmm:
            continue
        chain = hs.expand(m)
        if chain["strikes"] and chain["underlying_ltp"] is not None:
            return str(m["t"]), chain
    return None


def _is_session(minutes: Sequence[Mapping[str, Any]]) -> bool:
    """The live watch's holiday test: a Nifty that does not move in the first polls."""

    early = [m.get("ltp") for m in minutes if str(m["t"])[11:16] < "09:21"]
    return bool(minutes) and not (len(early) >= 3 and len(set(early)) <= 1)


def open_night(evening: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The position sold at the close, or None when the rule does not trade that evening."""

    if not _is_session(evening):
        return None
    got = _chain_at(evening, ENTRY_FROM)
    if got is None:
        return None
    now, chain = got
    expiry = str(chain.get("expiry"))
    if expiry <= now[:10]:
        return None  # the nearest contract expires today: nothing to carry
    strike = oi.atm_strike(chain)
    legs = {
        "short_ce": (strike, "ce", "bid"), "short_pe": (strike, "pe", "bid"),
        "long_ce": (strike + WING, "ce", "ask"), "long_pe": (strike - WING, "pe", "ask"),
    }  # fmt: skip
    entry = {}
    for name, (k, side, price) in legs.items():
        q = oi.quote(chain, k, side)
        if not q or q[price] <= 0:
            return None
        entry[name] = {"strike": k, "side": side, "entry": q[price]}
    return {"opened_at": now, "expiry": expiry, "spot": chain["underlying_ltp"], "legs": entry}


def close_night(
    night: Mapping[str, Any], morning: Sequence[Mapping[str, Any]], costs: oi.OptionCosts
) -> list[dict[str, Any]] | None:
    """Both books' rows for one night, or None when the morning cannot close it at all."""

    if not _is_session(morning):
        return None
    got = _chain_at([m for m in morning if str(m.get("expiry")) == night["expiry"]], EXIT_FROM)
    if got is None:
        return None
    now, chain = got
    legs = night["legs"]
    exits: dict[str, float | None] = {}
    for name, leg in legs.items():
        q = oi.quote(chain, leg["strike"], leg["side"])
        if name.startswith("short"):
            exits[name] = q["ask"] if q and q["ask"] > 0 else None
        else:
            exits[name] = max(q["bid"], 0.0) if q else None
    credit = (legs["short_ce"]["entry"] + legs["short_pe"]["entry"]
              - legs["long_ce"]["entry"] - legs["long_pe"]["entry"])  # fmt: skip
    cap = (WING - credit) * oi.LOT
    base = {
        "expiry": night["expiry"], "opened_at": night["opened_at"], "closed_at": now,
        "closed_on": now[:10], "spot_in": night["spot"], "spot_out": chain["underlying_ltp"],
        "nights": (date.fromisoformat(now[:10]) - date.fromisoformat(night["opened_at"][:10])).days,
    }  # fmt: skip

    def fees(names: Sequence[str]) -> float:
        total = 0.0
        for name in names:
            first, last = ("sell", "buy") if name.startswith("short") else ("buy", "sell")
            total += costs.trade(legs[name]["entry"], oi.LOT, first)
            if exits[name]:
                total += costs.trade(exits[name], oi.LOT, last)
        return total

    def gross(names: Sequence[str]) -> float:
        total = 0.0
        for name in names:
            sign = 1 if name.startswith("short") else -1
            total += sign * (legs[name]["entry"] - (exits[name] or 0.0)) * oi.LOT
        return total

    rows = []
    priced = all(v is not None for v in exits.values())
    charge = fees(tuple(legs))
    hedged = {**base, "rule": "night_hedged", "credit_points": round(credit, 2),
              "cap_inr": round(cap, 2), "costs_inr": round(charge, 2),
              "priced": priced}  # fmt: skip
    hedged["gross_inr"] = round(gross(tuple(legs)) if priced else -cap, 2)
    hedged["net_inr"] = round(hedged["gross_inr"] - charge, 2)
    rows.append(hedged)
    shorts = ("short_ce", "short_pe")
    if exits["short_ce"] is not None and exits["short_pe"] is not None:
        charge = fees(shorts)
        rows.append({**base, "rule": "night_plain", "costs_inr": round(charge, 2), "priced": True,
                     "gross_inr": round(gross(shorts), 2),
                     "net_inr": round(gross(shorts) - charge, 2)})  # fmt: skip
    else:
        rows.append({**base, "rule": "night_plain", "priced": False})
    for row in rows:
        row["legs"] = {k: {**v, "exit": exits[k]} for k, v in legs.items()
                       if row["rule"] == "night_hedged" or k in shorts}  # fmt: skip
    return rows


def replay(chains: Path, costs: oi.OptionCosts, *, start: date | None = None,
           end: date | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Every night between consecutive saved sessions whose entry evening is in [start, end]."""

    days = sorted(p.stem for p in chains.glob("*.jsonl"))
    out: list[dict[str, Any]] = []
    for evening, morning in zip(days, days[1:], strict=False):
        when = date.fromisoformat(evening)
        if (start and when < start) or (end and when > end):
            continue
        night = open_night(oi._minutes(chains, evening))
        if night is None:
            continue
        rows = close_night(night, oi._minutes(chains, morning), costs)
        if rows:
            out += rows
    return out


def judge(rows: Sequence[Mapping[str, Any]], needed: int) -> dict[str, Any]:
    """Totals of both books and the parts of the bar that night_hedged fails."""

    books = {}
    for name in BOOKS:
        mine = [r for r in rows if r.get("rule") == name]
        nets = [float(r["net_inr"]) for r in mine if "net_inr" in r]
        books[name] = {**hs.totals(nets), "unpriced": sum(1 for r in mine if not r["priced"])}
    mine = [r for r in rows if r.get("rule") == "night_hedged"]
    nets = [float(r["net_inr"]) for r in mine]
    over = [
        r["closed_on"]
        for r in mine
        if -float(r["net_inr"]) > float(r["cap_inr"]) + float(r["costs_inr"]) + 0.01
    ]
    book = books["night_hedged"]
    book["mean_credit_points"] = hs._mean([r["credit_points"] for r in mine])
    book["mean_cap_inr"] = hs._mean([r["cap_inr"] for r in mine])
    book["mean_costs_inr"] = hs._mean([r["costs_inr"] for r in mine])
    fails = []
    if len(nets) < needed:
        fails.append(f"{len(nets)} of {needed} nights")
    ratio = PROFIT_TO_LOSS if needed == NIGHTS_NEEDED else 1.0
    if not nets or book["total_profit"] <= ratio * -book["total_loss"]:
        fails.append("profit under 1.5 times loss" if ratio > 1 else "profit does not beat loss")
    if over:
        fails.append(f"beyond its cap on {over}")
    if needed == NIGHTS_NEEDED and sum(nets[-PAUSE_NIGHTS:]) <= 0:
        fails.append("last 20 nights not in profit")
    return {"books": books, "fails": fails}


def read_ledger(path: Path = LEDGER) -> list[dict[str, Any]]:
    return hs.read_ledger(path)


def forward(
    now: datetime | None = None,
    *,
    chains: Path = hs.CHAINS,
    ledger: Path = LEDGER,
    verdict: Path = VERDICT,
    costs: oi.OptionCosts | None = None,
) -> dict[str, Any]:
    """Append each night of the trial once it has closed, and write the verdict so far."""

    from tradedesk.broker.indstocks.models import IST

    now = now or datetime.now(IST)
    old = read_ledger(ledger)
    seen = {str(r.get("opened_at"))[:10] for r in old}
    fresh = [r for r in replay(chains, costs or hs.load_costs(), start=FROM, end=now.date())
             if r["opened_at"][:10] not in seen]  # fmt: skip
    for row in fresh:
        oi.append(ledger, {**row, "recorded_at": now.isoformat()})
    report = {"generated_at": now.isoformat(), "from": str(FROM), "new_rows": len(fresh),
              **judge([*old, *fresh], NIGHTS_NEEDED)}  # fmt: skip
    report["passed_bars_1_to_3"] = not report["fails"]
    verdict.parent.mkdir(parents=True, exist_ok=True)
    verdict.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def run() -> dict[str, Any]:
    """The replay of the nights saved before the forward trial's start."""

    rows = replay(hs.CHAINS, hs.load_costs(), end=date.fromordinal(FROM.toordinal() - 1))
    report = {**judge(rows, MIN_NIGHTS), "nights": rows}
    report["worth_a_forward_trial"] = not report["fails"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "replay.json").write_text(json.dumps(report, indent=1, default=str), "utf-8")
    return report


def main() -> None:
    r = run()
    for name, b in r["books"].items():
        extra = ""
        if "mean_cap_inr" in b:
            extra = (f", credit {b['mean_credit_points']} pts, cap Rs {b['mean_cap_inr']}, "
                     f"charges Rs {b['mean_costs_inr']} a night")  # fmt: skip
        print(f"{name}: {b['sessions']} nights, {b['won']} won, Rs {b['net_inr']:+,.0f} "
              f"(profit Rs {b['total_profit']:,.0f}, loss Rs {b['total_loss']:,.0f}), worst "
              f"Rs {b['worst_session']}, unpriced {b['unpriced']}{extra}")  # fmt: skip
    for row in r["nights"]:
        if row["rule"] == "night_hedged":
            plain = next((x.get("net_inr") for x in r["nights"] if x["rule"] == "night_plain"
                          and x["opened_at"] == row["opened_at"]), None)  # fmt: skip
            print(f"  {row['opened_at'][:10]} -> {row['closed_on']} ({row['nights']}n) Nifty "
                  f"{row['spot_in']:.0f} -> {row['spot_out']:.0f}: hedged {row['net_inr']:+,.0f}, "
                  f"plain {plain}, cap {row['cap_inr']:.0f}")  # fmt: skip
    print("worth a forward trial:", r["worth_a_forward_trial"], r["fails"])


if __name__ == "__main__":
    main()
