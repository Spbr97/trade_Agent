"""Run the probation desk for one market: record new capped calls, grade resolved ones, and
enforce the stop-loss-of-the-experiment rules. Calls are read from the signal tracker's log."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.config import load_config  # noqa: E402
from tradedesk.markets.market import bse_market, nse_market  # noqa: E402
from tradedesk.probation import load_probation, run_probation  # noqa: E402


def _rows(market: str) -> list[dict[str, Any]]:
    path = ROOT / "data" / "reports" / f"{market}_signal_tracking.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


async def _notify(calls: list[dict[str, Any]]) -> None:
    from tradedesk.alerts import TelegramBot, load_bot_token
    from tradedesk.alerts.cards import OutboundMessage

    settings = load_config(ROOT)
    token = load_bot_token()
    chat = settings.alerts.telegram.allowed_chat_id
    if not (settings.alerts.telegram.enabled and token and chat):
        return
    bot = TelegramBot(token=token, chat_id=int(chat))
    for c in calls:
        body = (
            f"{c['symbol']}  BUY above {c['entry']}\n"
            f"stop {c['stop']}   T1 {c['t1']}   T2 {c['t2']}\n"
            f"qty {c['qty']}  (risk Rs {c['risk_rupees']})\n{c['setup']}\n{c['label']}"
        )
        await bot.send(OutboundMessage(title="PROBATION call", body=body))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status"])
    parser.add_argument("--market", choices=["nse", "bse"], default="bse")
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(load_probation(args.market), indent=2, default=str))
        return
    settings = load_config(ROOT)
    market = (nse_market if args.market == "nse" else bse_market)(settings)
    rows = _rows(args.market)
    latest = max(date.fromisoformat(r["armed_on"]) for r in rows)
    before = {c["signal_id"] for c in load_probation(args.market).get("calls_today", [])}
    status = run_probation(
        args.market,
        rows=rows,
        cost_model=market.costs,
        capital=float(settings.risk.trading_capital),
        risk_pct=float(settings.risk.max_risk_per_trade_pct),
        max_value_pct=float(settings.risk.max_position_value_pct),
        latest_session=latest,
    )
    fresh = [c for c in status.get("calls_today", []) if c["signal_id"] not in before]
    if fresh and not args.no_notify:
        asyncio.run(_notify(fresh))
    print(json.dumps(status, indent=2, default=str))


if __name__ == "__main__":
    main()
