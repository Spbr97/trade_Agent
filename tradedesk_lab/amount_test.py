"""Does trade size rescue intraday? Every intraday result re-priced at each amount.

Owner, 2026-11-05: the agent's target is a few successful intraday trades that can run longer, and
it should be measured at each amount. Intraday charges fall from 0.156% at Rs 10,000 to 0.047% at
Rs 1 lakh, because brokerage has a Rs 5 cap per order. This asks whether anything lost only to
charges. The rules, entries and exits do not change, only the position's rupee size.

  playbook  tradedesk_lab/intraday_playbook.py's rules on the current 15-minute history, at a fixed
            amount per trade instead of its Rs 500 of risk. Same pass bar: +0.05R after charges,
            beats matched random entries (gross, so size cannot change it), 3+ positive quarters
            and the last one. The bar's alpha is not widened for the four amounts; anything that
            passes here is a lead for a forward test, not a result.
  fade      the forward results-pop short (results_reaction.run_fade_forward) at each amount, same
            status rules. The fade's own bar, judged at Rs 10,000, is not changed by this report.

Research only. Nothing here alerts, sizes or trades.

RESULT (2026-11-05, 15-minute history 2025-12-29 to 2026-11-04, 211 sessions, 1,733 stocks): size
does NOT rescue intraday. 0 of 16 variants pass at any of the four amounts. Most lose to random
timing before any charge. Closest at Rs 1 lakh: first_hour short, market-aligned, +0.024R (+Rs 78 a
trade), no better than random (p 0.245); pdh_retest short beats random (+0.035R, p 0.0) but makes
-0.022R after charges. The shorts' gross gains also come from a year when the market fell. Fade,
5 forward days in a rally, 297 trades: -Rs 15 a trade at Rs 10,000, -Rs 22 at 25,000, -Rs 31 at
50,000, -Rs 49 at Rs 1 lakh; against the market +0.17% rising to +0.28% a trade; worst open loss
on one trade Rs 1,575 up to Rs 15,752. Bigger trades lose less per rupee but more rupees.

    python -m tradedesk_lab.amount_test fade
    python -m tradedesk_lab.amount_test playbook
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import intraday_playbook as ip
from tradedesk_lab import results_reaction as rr
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "amount_test"
AMOUNTS = (10_000, 25_000, 50_000, 100_000)


def cost_model() -> Any:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel

    return EquityCostModel(load_config(Path(".")).risk.costs)


def intraday_cost(cm: Any, buy: float, sell: float, qty: int) -> float:
    """Total intraday charges in rupees for `qty` shares."""

    from tradedesk.models import TradeType

    return float(
        cm.round_trip_cost(
            trade_type=TradeType.INTRADAY, qty=qty,
            entry_price=Decimal(str(round(buy, 2))), exit_price=Decimal(str(round(sell, 2))),
        ).total
    )  # fmt: skip


def fade_at(rows: Iterable[Mapping[str, Any]], cm: Any, amount: int) -> list[dict[str, Any]]:
    """The fade's trades at `amount` a trade. A share dearer than the amount cannot be traded."""

    out = []
    for row in rows:
        entry, exit_px = float(row["entry"]), float(row["exit"])
        qty = int(amount // entry)
        if qty < 1:
            continue
        cost = intraday_cost(cm, exit_px, entry, qty) / (qty * entry)
        net = (entry - exit_px) / entry - cost
        out.append({**row, "net": net, "excess": float(row["excess"]) + net - float(row["net"]),
                    "rupees": net * qty * entry, "worst_rupees": -float(row["mae"]) * qty * entry})  # fmt: skip  # noqa: E501
    return out


def fade_table(rows: list[dict[str, Any]], cm: Any) -> dict[str, Any]:
    table = {}
    for amount in AMOUNTS:
        at = fade_at(rows, cm, amount)
        verdict = rr.fade_verdict(at)
        rupees = [r["rupees"] for r in at]
        table[str(amount)] = {
            **verdict, "mean_rupees": float(np.mean(rupees)) if rupees else None,
            "total_rupees": float(np.sum(rupees)),
            "worst_open_rupees": min((r["worst_rupees"] for r in at), default=None),
        }  # fmt: skip
    return table


def reprice(trades: Iterable[ip.Trade], cm: Any, amount: int) -> list[ip.Trade]:
    """Set each trade's net R and rupees at a fixed amount; a dearer share is left out."""

    kept = []
    for t in trades:
        qty = int(amount // t.entry)
        risk = abs(t.entry - t.stop)
        if qty < 1 or risk <= 0:
            continue
        exit_px = t.entry + t.gross_r * (t.entry - t.stop)
        buy, sell = (t.entry, exit_px) if t.direction == "long" else (exit_px, t.entry)
        t.net_r = t.gross_r - intraday_cost(cm, buy, sell, qty) / (qty * risk)
        t.rupees = t.net_r * qty * risk  # type: ignore[attr-defined]
        kept.append(t)
    return kept


def _in_variant(t: ip.Trade, variant: str) -> bool:
    return variant == "all" or (t.market_move > 0) == (t.direction == "long")


def playbook_table(cm: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    real = ip.evaluate

    def grab(trades: list[ip.Trade], market: dict[Any, float]) -> dict[str, Any]:
        captured.update(trades=trades, market=market)
        return {}

    ip.evaluate = grab  # run() replays the rules, then hands its trades to evaluate
    try:
        ip.run()
    finally:
        ip.evaluate = real
    ip.OUT = OUT_DIR / "playbook"
    table: dict[str, Any] = {}
    for amount in AMOUNTS:
        kept = reprice(captured["trades"], cm, amount)
        report = ip.evaluate(kept, captured["market"])
        rows = []
        for r in report["results"]:
            if not r.get("trades"):
                continue
            mine = [
                t.rupees for t in kept  # type: ignore[attr-defined]
                if t.rule == r["rule"] and t.direction == r["direction"]
                and _in_variant(t, r["variant"])
            ]  # fmt: skip
            rows.append({
                k: r.get(k) for k in (
                    "rule", "direction", "variant", "trades", "mean_net_r", "mean_gross_r",
                    "beats_random_by_r", "p_random", "alpha", "positive_quarters", "passed",
                )
            } | {"mean_rupees": float(np.mean(mine))})  # fmt: skip
        table[str(amount)] = {"data": report["data"], "rows": rows}
        passed = [f"{x['variant']} {x['rule']} {x['direction']}" for x in rows if x["passed"]]
        print(amount, "passed:", passed or "none", flush=True)
        for x in rows:
            print(json.dumps(x, default=str), flush=True)
    return table


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=("fade", "playbook"))
    args = parser.parse_args(argv)
    cm = cost_model()
    if args.command == "fade":
        rows = [json.loads(x) for x in rr.FADE_LEDGER.read_text(encoding="utf-8").splitlines() if x]
        table = fade_table(rows, cm)
        print(json.dumps(table, indent=1, default=str))
    else:
        table = playbook_table(cm)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {"generated_at": datetime.now(IST).isoformat(), "amounts": table}
    path = OUT_DIR / f"{args.command}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("report:", path)


if __name__ == "__main__":
    main()
