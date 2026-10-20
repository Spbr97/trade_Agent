"""Monthly BTC/ETH plan, paper only (owner's request 2026-10-21, kept beside the normal calls).

Why: tradedesk_lab/crypto_multiday_alternatives.py found that on 2023-2026, after the 0.57%
round trip and 30% tax on every winning sale (no loss offset), simply holding BTC beat every
crypto trading rule tested (x4.28 vs at most x2.42), because a holder pays tax once and a
trader pays it on every winner. It is an investment plan, not a trading edge: BTC still fell
47% at its worst in that period.

Rule: in each calendar month, on the first run with fresh prices, "buy" Rs 5,000 of BTC and
Rs 5,000 of ETH at the last COMPLETED daily close, paying the 0.2% fee + 18% GST and 0.05%
slippage. Nothing is ever sold. The plan's value is shown two ways: at today's close, and as
if sold today after everything cut at the sale (fee + GST, slippage and the 1% TDS). Year-end
income tax is not deducted (owner's rule, 2026-10-21); the research result above was judged
after it.

Stale prices (no completed close within 2 days) record nothing; the next run tries again.
Nothing here places an order.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST

DB = Path("data/crypto.duckdb")
LEDGER = Path("data/reports/crypto_monthly_plan.jsonl")
COINS = {"CDX_BTCINR": "BTC", "CDX_ETHINR": "ETH"}
AMOUNT_INR = 5_000.0
FEE = 0.002 * 1.18
SLIP = 0.0005
TDS = 0.01  # cut from the sale value at the sale
MAX_AGE_DAYS = 2


def last_closes(db: Path, today: date, *, tries: int = 20) -> dict[str, tuple[date, float]]:
    """The last completed daily close (a bar dated before `today`) per coin."""
    import duckdb

    for attempt in range(tries):
        try:
            con = duckdb.connect(str(db), read_only=True)
            break
        except duckdb.IOException:
            if attempt == tries - 1:
                raise
            time.sleep(15)
    try:
        out = {}
        for code in COINS:
            rows = con.execute(
                "SELECT ts, close FROM candles WHERE scrip_code = ? AND interval = '1day' "
                "ORDER BY ts DESC LIMIT 5",
                [code],
            ).fetchall()
            for ts, close in rows:
                day = datetime.fromtimestamp(int(ts), IST).date()
                if day < today and close and close > 0:
                    out[code] = (day, float(close))
                    break
        return out
    finally:
        con.close()


def read(path: Path = LEDGER) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def value(lots: list[dict[str, Any]], prices: dict[str, float]) -> dict[str, float]:
    invested = worth = if_sold = 0.0
    for lot in lots:
        price = prices.get(lot["code"])
        if price is None:
            continue
        invested += lot["amount_inr"]
        now = lot["qty"] * price
        worth += now
        if_sold += now * (1 - SLIP) * (1 - FEE) - now * TDS
    return {"invested": invested, "worth": worth, "if_sold": if_sold}


def run(
    *, db: Path = DB, ledger: Path = LEDGER, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(IST)
    today = now.date()
    month = f"{today:%Y-%m}"
    lots = read(ledger)
    closes = last_closes(db, today)
    fresh = {
        c: px for c, (day, px) in closes.items() if today - day <= timedelta(days=MAX_AGE_DAYS)
    }
    bought = []
    if all(lot.get("month") != month for lot in lots) and len(fresh) == len(COINS):
        for code, price in fresh.items():
            qty = AMOUNT_INR * (1 - FEE) / (price * (1 + SLIP))
            bought.append(
                {
                    "month": month,
                    "code": code,
                    "coin": COINS[code],
                    "price": price,
                    "price_day": closes[code][0].isoformat(),
                    "amount_inr": AMOUNT_INR,
                    "qty": qty,
                    "at": now.isoformat(),
                }
            )
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            for row in bought:
                fh.write(json.dumps(row) + "\n")
        lots += bought
    return {
        "month": month,
        "bought": bought,
        "stale": len(fresh) < len(COINS),
        "months": len({lot["month"] for lot in lots}),
        "prices": fresh,
        "totals": value(lots, fresh),
    }


def message(result: dict[str, Any]) -> tuple[str, str] | None:
    """The month's push, only in the run that records its buy."""
    if not result["bought"]:
        return None
    buys = ", ".join(
        f"Rs {b['amount_inr']:,.0f} {b['coin']} at about Rs {b['price']:,.0f}"
        for b in result["bought"]
    )
    t = result["totals"]
    return (
        f"Crypto monthly plan {result['month']}",
        "\n".join(
            [
                f"This month: {buys} (last completed daily close).",
                f"Plan so far: {result['months']} month(s), invested Rs {t['invested']:,.0f}, "
                f"worth Rs {t['worth']:,.0f}; Rs {t['if_sold']:,.0f} in hand if sold today "
                "(after fee, GST and 1% TDS; year-end tax not deducted).",
                "Why: on 2023-2026 holding BTC beat every crypto trading rule after tax "
                "(x4.28 vs at most x2.42) - but BTC also fell 47% at its worst. Paper plan, "
                "kept beside the normal crypto calls.",
                "RESEARCH ONLY, not advice or an alert.",
            ]
        ),
    )
