"""Forward paper trial: short strong results at the open, cover at the close.

tradedesk_lab/results_reading.py found this trade making about +0.5% a call with about 60% wins in
both a held-out period (2019-2023) and later data, but it FAILED its bar: +0.43% over the market,
t 2.33 against 3.0, negative in the second half. This does not overturn that. It only collects new
evidence on sessions no test has seen. Owner approved the trial on 2026-11-07. Research only: it
never alerts, sizes or trades, and a pass here is a proposal to the owner, not a live call.

Rule, unchanged from the study: a results filing published while the market was shut, profit up 25%+
and sales up 10%+ on the same quarter a year before, both years' profit positive, 20-session traded
value >= Rs 5 crore, price >= Rs 20. Short at the next session's open, cover at that close, 0.05%
slippage each way, a Rs 10,000 trade's intraday charges. No stop, as in the study; `mae` records the
worst move against each trade, so a stop can be judged later.

PASS BAR (written 2026-11-07, before the first forward trade, FROM 2026-11-09), the fade's own
(results_reaction.fade_verdict): at least 150 trades over at least 20 days; mean net > 0 after
charges at Rs 10,000; mean excess over the market > 0, t >= 2.5, one DAY as one unit; positive in
both halves of the forward period. The other amounts are reported beside it and never decide it.

What real use would still need, and this trial does not prove: the signal has to be read before the
09:08 pre-open, and the stock has to be available to short intraday at the broker. This job records
after the close, from the publication minute, so it measures the rule, not that delivery.

    python scripts/results_short_forward.py
"""

from __future__ import annotations

import json
import math
import time as time_module
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import amount_test as am
from tradedesk_lab import event_studies as es
from tradedesk_lab import new_listings as nl
from tradedesk_lab import results_reaction as fade
from tradedesk_lab import results_reading as rr
from tradedesk_lab.artifacts import OUTPUT

FROM = date(2026, 11, 9)
OUT_DIR = OUTPUT / "results_short"
LEDGER = OUT_DIR / "forward.jsonl"
VERDICT = OUT_DIR / "forward.json"
LOOK_BACK = timedelta(days=7)  # a filing from the weekend before FROM can enter on FROM


def refresh_index(today: date, *, pause_seconds: float = 1.5) -> None:
    """Fetch the integrated list again for this month and the last, whose saved copies are stale:
    filings keep arriving after a month's file was first written."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    folder = rr.INDEX_DIR / "new"
    folder.mkdir(parents=True, exist_ok=True)
    last_month = today.replace(day=1) - timedelta(days=1)
    with httpx.Client(headers=_NSE_HEADERS, timeout=180, follow_redirects=True) as client:
        client.get("https://www.nseindia.com/")  # sets the cookies the API requires
        for first, last in rr._month_ranges(last_month.replace(day=1), today):
            response = client.get(rr.NEW_URL, params={
                "index": "equities", "from_date": first.strftime("%d-%m-%Y"),
                "to_date": last.strftime("%d-%m-%Y"), "type": rr.NEW_TYPE, "size": 5000,
            })  # fmt: skip
            response.raise_for_status()
            body = response.json()
            rows = body.get("data", []) if isinstance(body, dict) else body
            (folder / f"{first:%Y-%m}.json").write_text(json.dumps(rows), encoding="utf-8")
            time_module.sleep(pause_seconds)


def outcome(measure: rr.Measure, code: str, e: int) -> dict[str, Any] | None:
    """The trade at its Rs 10,000 size, or None if that session has no usable prices yet."""

    j = measure.column[code]
    raw, close = float(measure.opens[e, j]), float(measure.closes[e, j])
    high = float(measure.p.high.iat[e, j])
    if not (math.isfinite(raw) and math.isfinite(close) and raw > 0):
        return None
    entry, exit_px = raw * (1 - rr.SLIPPAGE), close * (1 + rr.SLIPPAGE)
    net = (entry - exit_px) / entry - measure.intraday(exit_px, entry)
    return {
        "scrip_code": code, "day": str(measure.p.close.index[e]), "entry": entry, "exit": exit_px,
        "net": net, "excess": net + measure.market(e, e),
        "mae": high / entry - 1 if math.isfinite(high) else 0.0,
    }  # fmt: skip


def new_trades(
    table: Mapping[tuple[str, date], Mapping[str, Any]], measure: rr.Measure,
    codes: Mapping[str, str], seen: set[str], *, start: date = FROM,
) -> list[dict[str, Any]]:  # fmt: skip
    """Every qualifying trade from `start` on not yet in the ledger, oldest first."""

    sessions = list(measure.p.close.index)
    out = []
    for symbol, quarter in sorted(table):
        key = f"{symbol}:{quarter}"
        published = datetime.fromisoformat(str(table[(symbol, quarter)]["published"]))
        if key in seen or published.date() < start - LOOK_BACK:
            continue
        code = codes.get(symbol)
        if code not in measure.column:
            continue
        numbers = rr.classify(table, symbol, quarter)
        if numbers is None or not numbers["strong"]:
            continue
        e = rr.entry_session(sessions, published)
        if e is None or e < 1 or sessions[e] < start:
            continue
        j = measure.column[code]
        before, usual = float(measure.closes[e - 1, j]), float(measure.turnover.iat[e, j])
        if not (math.isfinite(before) and math.isfinite(usual)):
            continue
        if usual < rr.MIN_TURNOVER or before < rr.MIN_PRICE:
            continue
        trade = outcome(measure, code, e)
        if trade:
            out.append({"key": key, "symbol": symbol, **trade})
    return sorted(out, key=lambda r: (r["day"], r["key"]))


def judge(rows: list[dict[str, Any]], cm: Any) -> dict[str, Any]:
    """The fade's forward bar, at Rs 10,000; each other amount beside it for information."""

    table = am.fade_table(rows, cm) if rows else {}
    return {**fade.fade_verdict(rows), "by_amount": table}


def read_ledger(path: Path = LEDGER) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run(*, now: datetime | None = None, fetch: bool = True) -> dict[str, Any]:
    """Nightly: read new filings, record each new trade once, judge the forward record."""

    now = now or datetime.now(IST)
    panels = nl.load()
    codes = es.symbols()
    if fetch:
        refresh_index(now.date())
        rr.fetch_figures(rr.liquid_symbols(panels, codes), since=FROM - LOOK_BACK)
    measure = rr.Measure(panels)
    table = rr.one_per_quarter(rr.read_figures())
    ledger = read_ledger()
    new = new_trades(table, measure, codes, {r["key"] for r in ledger})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if new:
        with LEDGER.open("a", encoding="utf-8") as fh:
            for row in new:
                fh.write(json.dumps({**row, "recorded_at": now.isoformat()}) + "\n")
    verdict = {
        "generated_at": now.isoformat(), "from": str(FROM), "new": len(new),
        **judge([*ledger, *new], measure.cm),
    }  # fmt: skip
    VERDICT.write_text(json.dumps(verdict, indent=2, default=str), encoding="utf-8")
    print(json.dumps(verdict, default=str))
    return verdict
