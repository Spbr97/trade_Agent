"""Forward paper test of two option-income rules on REAL Nifty quotes (owner, 2026-10-19).

Research only: it never places an order. There is no history to backtest against - INDstocks'
expired-contract candles return nothing yet (probed 2026-10-19) and the live chain has no past -
so the only honest test is forward. The watch polls the Nifty option chain once a minute from
09:17 to 15:20, keeps every minute's chain (data/m14_m18/option_income/chain/<date>.jsonl, the
history INDstocks lacks) and paper-trades, with fills at the side of the book a real order hits
(sell at the bid, buy back at the ask):

  straddle_30 / straddle_40   at the first poll from 09:20, sell the ATM call and put of the
              nearest expiry. Each leg has its own stop at +30% (or +40%) of its entry premium,
              bought back at the ask once the ask reaches it. Whatever is open is bought back at
              the ask at 15:15. Nothing is held overnight.
  condor      every Friday from 09:30 (one at a time): for the nearest expiry after today, sell
              the call and the put nearest 0.20 delta, buy the call 200 points above and the put
              200 points below. No stop - the wings cap the loss. Settles at expiry against the
              last Nifty price of the day (intrinsic value per leg).

Costs per leg trade (config/risk.yaml `option_costs`, statutory NSE rates; brokerage PROVISIONAL
until checked against a contract note): STT on the sell premium, exchange and SEBI fees on both
sides, stamp duty on the buy, GST on brokerage + exchange + SEBI, brokerage per order. One lot.
A day on which Nifty does not move in the first polls is treated as a holiday and not traded.
Judge after 60 straddle sessions and 12 condor weeks: mean net per lot, worst day, worst week.

Stress (2026-10-31): selling straddles wins small and often, so the win rate hides the risk. Each
straddle records its worst open loss of the day (marked at the ask every minute) and the summary
reports, per rule: that worst point, what a day with BOTH legs stopped costs (the whipsaw the
per-leg stops cannot prevent) in average winning days, how often both legs stopped, and how far
past the stop the fills landed (a jump between minute polls fills beyond the stop). Trades closed
before the field existed are replayed from the saved minute chains.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tradedesk_lab.artifacts import OUTPUT

ROOT = OUTPUT / "option_income"
LEDGER = ROOT / "ledger.jsonl"
CONDOR_STATE = ROOT / "condor_open.json"
LOT = 65  # NIFTY lot size in data/instruments/fno.csv (2026)
STOPS = {"straddle_30": 0.30, "straddle_40": 0.40}
CONDOR_DELTA = 0.20
CONDOR_WING = 200


@dataclass(frozen=True)
class OptionCosts:
    stt_sell_pct: float = 0.001  # options: 0.1% of the sell premium (from 2024-10-01)
    exchange_pct: float = 0.0003503  # NSE options, both sides
    sebi_pct: float = 0.000001
    stamp_buy_pct: float = 0.00003
    gst_pct: float = 0.18
    brokerage_per_order: float = 20.0  # PROVISIONAL

    def trade(self, premium: float, qty: int, side: str) -> float:
        """Rupee cost of one order: `side` is "sell" or "buy"."""

        value = premium * qty
        exchange, sebi = value * self.exchange_pct, value * self.sebi_pct
        cost = self.brokerage_per_order + exchange + sebi
        cost += (self.brokerage_per_order + exchange + sebi) * self.gst_pct
        cost += value * (self.stt_sell_pct if side == "sell" else self.stamp_buy_pct)
        return round(cost, 2)


def load_costs(config: Mapping[str, Any] | None) -> OptionCosts:
    return OptionCosts(**{k: float(v) for k, v in (config or {}).items()})


# --------------------------------------------------------------------------- chain helpers


def quote(chain: Mapping[str, Any], strike: float, side: str) -> dict[str, float] | None:
    leg = (chain.get("strikes") or {}).get(_key(chain, strike), {}).get(side)
    if not leg:
        return None
    return {
        "bid": float(leg.get("top_bid_price") or 0),
        "ask": float(leg.get("top_ask_price") or 0),
        "last": float(leg.get("last_price") or 0),
        "delta": float((leg.get("greeks") or {}).get("delta") or 0),
    }


def _key(chain: Mapping[str, Any], strike: float) -> str:
    for k in chain.get("strikes") or {}:
        if abs(float(k) - strike) < 1e-6:
            return k
    return ""


def atm_strike(chain: Mapping[str, Any]) -> float:
    ltp = float(chain["underlying_ltp"])
    return min((float(k) for k in chain["strikes"]), key=lambda k: abs(k - ltp))


def compact(chain: Mapping[str, Any]) -> dict[str, Any]:
    """The minute record kept on disk: bid, ask, last, iv, delta per strike and side."""

    out = {}
    for k, legs in (chain.get("strikes") or {}).items():
        out[k] = {
            side: [
                leg.get("top_bid_price"),
                leg.get("top_ask_price"),
                leg.get("last_price"),
                leg.get("iv"),
                (leg.get("greeks") or {}).get("delta"),
            ]  # fmt: skip
            for side, leg in legs.items()
            if leg
        }
    return {"ltp": chain.get("underlying_ltp"), "expiry": chain.get("expiry"), "strikes": out}


# --------------------------------------------------------------------------- straddle


@dataclass
class Leg:
    side: str  # "ce" | "pe"
    strike: float
    entry: float
    stop: float
    exit: float | None = None
    exit_at: str | None = None
    reason: str | None = None


@dataclass
class Straddle:
    name: str
    expiry: str
    opened_at: str
    legs: list[Leg] = field(default_factory=list)
    worst_open_inr: float = 0.0  # lowest gross mark-to-market of the day, at the ask

    def step(self, chain: Mapping[str, Any], now: str, *, square_off: bool) -> None:
        for leg in self.legs:
            if leg.exit is not None:
                continue
            q = quote(chain, leg.strike, leg.side)
            if not q or q["ask"] <= 0:
                continue
            if q["ask"] >= leg.stop:
                leg.exit, leg.exit_at, leg.reason = q["ask"], now, "stop"
            elif square_off:
                leg.exit, leg.exit_at, leg.reason = q["ask"], now, "15:15"
        mark = open_value(self.legs, chain)
        if mark is not None:
            self.worst_open_inr = min(self.worst_open_inr, mark)

    @property
    def done(self) -> bool:
        return all(leg.exit is not None for leg in self.legs)

    def result(self, costs: OptionCosts) -> dict[str, Any]:
        gross = sum((leg.entry - (leg.exit or leg.entry)) * LOT for leg in self.legs)
        fees = sum(costs.trade(leg.entry, LOT, "sell") + costs.trade(leg.exit or 0, LOT, "buy")
                   for leg in self.legs)  # fmt: skip
        return {
            "rule": self.name,
            "expiry": self.expiry,
            "opened_at": self.opened_at,
            "gross_inr": round(gross, 2),
            "costs_inr": round(fees, 2),
            "net_inr": round(gross - fees, 2),
            "worst_open_inr": round(self.worst_open_inr, 2),
            "both_stopped_inr": round(both_stopped(self.legs, costs), 2),
            "legs": [asdict(x) for x in self.legs],
        }


def open_value(legs: list[Leg], chain: Mapping[str, Any]) -> float | None:
    """Gross rupees of the straddle now: closed legs at their exit, open legs bought back at
    the ask (None while a quote is missing)."""

    total = 0.0
    for leg in legs:
        price = leg.exit
        if price is None:
            q = quote(chain, leg.strike, leg.side)
            if not q or q["ask"] <= 0:
                return None
            price = q["ask"]
        total += (leg.entry - price) * LOT
    return total


def both_stopped(legs: list[Leg], costs: OptionCosts) -> float:
    """Net rupees of the whipsaw day: both legs bought back exactly at their stops."""

    return sum((leg.entry - leg.stop) * LOT - costs.trade(leg.entry, LOT, "sell")
               - costs.trade(leg.stop, LOT, "buy") for leg in legs)  # fmt: skip


def open_straddles(chain: Mapping[str, Any], now: str) -> list[Straddle]:
    strike = atm_strike(chain)
    ce, pe = quote(chain, strike, "ce"), quote(chain, strike, "pe")
    if not ce or not pe or ce["bid"] <= 0 or pe["bid"] <= 0:
        return []
    return [
        Straddle(
            name,
            str(chain.get("expiry")),
            now,
            [
                Leg("ce", strike, ce["bid"], ce["bid"] * (1 + pct)),
                Leg("pe", strike, pe["bid"], pe["bid"] * (1 + pct)),
            ],
        )  # fmt: skip
        for name, pct in STOPS.items()
    ]


# --------------------------------------------------------------------------- condor


def open_condor(chain: Mapping[str, Any], now: str) -> dict[str, Any] | None:
    strikes = sorted(float(k) for k in chain.get("strikes") or {})

    def nearest(side: str, target: float) -> float | None:
        best = [(abs(abs(q["delta"]) - target), k) for k in strikes
                if (q := quote(chain, k, side)) and q["bid"] > 0 and q["delta"]]  # fmt: skip
        return min(best)[1] if best else None

    short_call, short_put = nearest("ce", CONDOR_DELTA), nearest("pe", CONDOR_DELTA)
    if short_call is None or short_put is None:
        return None
    long_call, long_put = short_call + CONDOR_WING, short_put - CONDOR_WING
    legs = {"short_ce": (short_call, "ce", "sell"), "short_pe": (short_put, "pe", "sell"),
            "long_ce": (long_call, "ce", "buy"), "long_pe": (long_put, "pe", "buy")}  # fmt: skip
    priced = {}
    for name, (strike, side, action) in legs.items():
        q = quote(chain, strike, side)
        price = (q or {}).get("bid" if action == "sell" else "ask") or 0.0
        if price <= 0:
            return None
        priced[name] = {"strike": strike, "side": side, "action": action, "price": price}
    credit = priced["short_ce"]["price"] + priced["short_pe"]["price"]
    credit -= priced["long_ce"]["price"] + priced["long_pe"]["price"]
    if credit <= 0:
        return None
    return {
        "expiry": chain.get("expiry"),
        "opened_at": now,
        "legs": priced,
        "credit": round(credit, 2),
        "max_loss_inr": round((CONDOR_WING - credit) * LOT, 2),
    }


def settle_condor(condor: Mapping[str, Any], spot: float, costs: OptionCosts) -> dict[str, Any]:
    """Expiry: each leg is worth its intrinsic value against the last Nifty price."""

    gross, fees = 0.0, 0.0
    for leg in condor["legs"].values():
        intrinsic = max(0.0, spot - leg["strike"]) if leg["side"] == "ce" else max(
            0.0, leg["strike"] - spot)  # fmt: skip
        sign = 1 if leg["action"] == "sell" else -1
        gross += sign * (leg["price"] - intrinsic) * LOT
        fees += costs.trade(leg["price"], LOT, leg["action"])
        if intrinsic > 0:  # exercised at expiry: STT on the intrinsic value of an ITM long
            fees += intrinsic * LOT * 0.00125 if leg["action"] == "buy" else 0.0
    return {"rule": "condor", "expiry": condor["expiry"], "opened_at": condor["opened_at"],
            "settle_spot": spot, "credit": condor["credit"], "gross_inr": round(gross, 2),
            "costs_inr": round(fees, 2), "net_inr": round(gross - fees, 2)}  # fmt: skip


# --------------------------------------------------------------------------- records


def append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _minutes(chain_dir: Path, day: str) -> list[dict[str, Any]]:
    path = chain_dir / f"{day}.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def replay_worst(row: Mapping[str, Any], minutes: list[dict[str, Any]]) -> float | None:
    """The worst open loss of a closed straddle, from that day's saved minute chains (the
    compact form: strikes[k][side] = [bid, ask, last, iv, delta])."""

    legs = [Leg(**leg) for leg in row.get("legs") or []]
    if not legs or not minutes:
        return None
    worst, seen = 0.0, False
    for m in minutes:
        t = str(m.get("t"))
        if t < str(row["opened_at"]) or m.get("expiry") != row.get("expiry"):
            continue
        total = 0.0
        for leg in legs:
            if leg.exit is not None and leg.exit_at is not None and t >= leg.exit_at:
                price = leg.exit
            else:
                k = next((k for k in m["strikes"] if abs(float(k) - leg.strike) < 1e-6), None)
                q = (m["strikes"].get(k) or {}).get(leg.side) if k else None
                if not q or not q[1]:
                    break
                price = float(q[1])
            total += (leg.entry - price) * LOT
        else:
            worst, seen = min(worst, total), True
    return worst if seen else None


def stress(
    rows: list[dict[str, Any]], costs: OptionCosts | None = None, chain_dir: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Per straddle rule: the risk a high win rate hides (see the module docstring)."""

    costs = costs or OptionCosts()
    chain_dir = chain_dir or ROOT / "chain"
    days: dict[str, list[dict[str, Any]]] = {}
    out = {}
    for rule in STOPS:
        mine = [r for r in rows if r.get("rule") == rule and r.get("legs")]
        if not mine:
            continue
        worst, whipsaw, both, overshoot = [], [], 0, []
        for r in mine:
            legs = [Leg(**leg) for leg in r["legs"]]
            w = r.get("worst_open_inr")
            if w is None:
                day = str(r.get("closed_on"))
                w = replay_worst(r, days.setdefault(day, _minutes(chain_dir, day)))
            if w is not None:
                worst.append(w)
            whipsaw.append(r.get("both_stopped_inr") or both_stopped(legs, costs))
            stopped = [x for x in legs if x.reason == "stop"]
            both += len(stopped) == len(legs)
            overshoot += [x.exit / x.stop - 1 for x in stopped if x.exit and x.stop]
        wins = [r["net_inr"] for r in mine if r["net_inr"] > 0]
        typical = sum(whipsaw) / len(whipsaw)
        out[rule] = {
            "sessions": len(mine),
            "worst_open_inr": min(worst) if worst else None,
            "both_stopped_inr": typical,
            "both_stopped_in_wins": -typical / (sum(wins) / len(wins)) if wins else None,
            "both_stopped_days": both,
            "stop_fill_overshoot": sum(overshoot) / len(overshoot) if overshoot else None,
            "stops": len(overshoot),
        }
    return out


def summary(
    ledger: Path = LEDGER, today: date | None = None, chain_dir: Path | None = None
) -> tuple[str, str]:
    rows = []
    if ledger.exists():
        rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x]
    day = (today or datetime.now().date()).isoformat()
    lines = []
    for rule in (*STOPS, "condor"):
        mine = [r for r in rows if r.get("rule") == rule]
        if not mine:
            continue
        nets = [r["net_inr"] for r in mine]
        todays = [r for r in mine if str(r.get("closed_on")) == day]
        today_txt = f"today {todays[-1]['net_inr']:+,.0f}, " if todays else ""
        lines.append(
            f"{rule}: {today_txt}{len(nets)} closed, total Rs {sum(nets):+,.0f}, "
            f"avg {sum(nets) / len(nets):+,.0f}, worst {min(nets):+,.0f} per lot"
        )
    for rule, st in stress(rows, chain_dir=chain_dir).items():
        bits = []
        if st["worst_open_inr"] is not None:
            bits.append(f"worst point in a day Rs {st['worst_open_inr']:+,.0f}")
        whip = f"a both-legs-stopped day about Rs {st['both_stopped_inr']:+,.0f}"
        if st["both_stopped_in_wins"] is not None:
            whip += f" (= {st['both_stopped_in_wins']:.1f} average winning days)"
        bits.append(whip)
        bits.append(f"both stopped on {st['both_stopped_days']} of {st['sessions']} days")
        if st["stop_fill_overshoot"] is not None:
            bits.append(f"stops filled {100 * st['stop_fill_overshoot']:+.0f}% past the stop "
                        f"({st['stops']} stops)")  # fmt: skip
        lines.append(f"{rule} risk: " + ", ".join(bits))
    if CONDOR_STATE.exists():
        c = json.loads(CONDOR_STATE.read_text(encoding="utf-8"))
        lines.append(
            f"condor open to {c['expiry']}: credit {c['credit']}, max loss Rs "
            f"{c['max_loss_inr']:,.0f} per lot"
        )
    lines.append(
        "PAPER ONLY on real quotes, 1 Nifty lot (65); not an alert. Brokerage is an estimate "
        f"(Rs {OptionCosts().brokerage_per_order:.0f} an order) until a real F&O contract note "
        "confirms it."
    )
    return "Option income paper test", "\n".join(lines or ["no closed paper trades yet"])
