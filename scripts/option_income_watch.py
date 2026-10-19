"""Minute-by-minute paper test of the straddle and iron-condor income rules on real Nifty quotes.

See tradedesk_lab/option_income.py for the rules. Runs weekdays from 09:17 (scheduled task
tradedesk-option-income-watch) until 15:20, then pushes the paper results to the phone. Read-only
towards the broker: option-chain and expiry calls only, through the shared rate limiter.

    python scripts/option_income_watch.py [--until 15:20]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402
from tradedesk_lab import option_income as oi  # noqa: E402

from tradedesk.broker.indstocks.models import IST  # noqa: E402

UNDERLYING, UNDERLYING_SCRIP = "NIFTY", "40000001"
STRIKE_COUNT = 20
COSTS_FILE = ROOT / "config" / "option_costs.yaml"


async def _chain(client: Any, expiry: str) -> dict[str, Any] | None:
    try:
        return await client.option_chain(
            exchange="NSE", segment="INDEX", underlying_scrip=UNDERLYING_SCRIP,
            expiry=expiry, strike_count=STRIKE_COUNT,
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - one missed minute must not end the session
        print(f"chain {expiry} failed: {exc!r}")
        return None


async def watch(client: Any, until: str) -> None:
    raw = yaml.safe_load(COSTS_FILE.read_text(encoding="utf-8")) if COSTS_FILE.exists() else {}
    costs = oi.load_costs((raw or {}).get("option_costs"))
    today = datetime.now(IST).date()
    expiries = sorted(await client.list_expiries(UNDERLYING))
    nearest = expiries[0]
    after_today = next((e for e in expiries if e > today.isoformat()), nearest)
    condor = (
        json.loads(oi.CONDOR_STATE.read_text(encoding="utf-8"))
        if oi.CONDOR_STATE.exists() else None
    )  # fmt: skip
    straddles: list[oi.Straddle] = []
    recorded: set[str] = set()
    opened, early_ltps = False, []
    last_ltp: float | None = None
    chain_file = oi.ROOT / "chain" / f"{today.isoformat()}.jsonl"
    print(f"{today}: nearest expiry {nearest}, next after today {after_today}, condor {condor}")
    while True:
        now = datetime.now(IST)
        hhmm = now.strftime("%H:%M")
        if hhmm > until:
            break
        stamp = now.isoformat(timespec="seconds")
        wanted = {nearest}
        if condor:
            wanted.add(condor["expiry"])
        if not condor and now.weekday() == 4:
            wanted.add(after_today)
        chains = {}
        for expiry in sorted(wanted):
            chain = await _chain(client, expiry)
            if chain and chain.get("strikes"):
                chains[expiry] = chain
                oi.append(chain_file, {"t": stamp, **oi.compact(chain)})
        main = chains.get(nearest)
        if main:
            last_ltp = float(main["underlying_ltp"])
            if hhmm < "09:21":
                early_ltps.append(last_ltp)
            if hhmm >= "09:21" and len(set(early_ltps)) <= 1 and len(early_ltps) >= 3:
                print("Nifty did not move after the open: market closed today, nothing traded")
                return
            if not opened and hhmm >= "09:20":
                straddles, opened = oi.open_straddles(main, stamp), True
                print("straddles opened:", [asdict_short(s) for s in straddles])
            for s in straddles:
                s.step(main, stamp, square_off=hhmm >= "15:15")
                if s.done and s.name not in recorded:
                    oi.append(oi.LEDGER, {**s.result(costs), "closed_on": today.isoformat()})
                    recorded.add(s.name)
        if not condor and now.weekday() == 4 and hhmm >= "09:30" and after_today in chains:
            condor = oi.open_condor(chains[after_today], stamp)
            if condor:
                oi.CONDOR_STATE.parent.mkdir(parents=True, exist_ok=True)
                oi.CONDOR_STATE.write_text(json.dumps(condor, indent=1), encoding="utf-8")
                oi.append(oi.LEDGER, {"event": "condor_opened", **condor})
                print("condor opened:", condor)
        await asyncio.sleep(max(1.0, 60 - datetime.now(IST).second))
    if condor and condor["expiry"] == today.isoformat() and last_ltp is not None:
        result = oi.settle_condor(condor, last_ltp, costs)
        oi.append(oi.LEDGER, {**result, "closed_on": today.isoformat()})
        oi.CONDOR_STATE.unlink(missing_ok=True)
        print("condor settled:", result)
    if opened:
        from tradedesk.task_health import alert

        alert(*oi.summary(today=today))


def asdict_short(s: oi.Straddle) -> str:
    return f"{s.name} {s.legs[0].strike:.0f} CE {s.legs[0].entry} PE {s.legs[1].entry}"


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--until", default="15:20")
    args = parser.parse_args()
    from tradedesk.cli import _with_client

    async def go(client: Any) -> None:
        await watch(client, args.until)

    asyncio.run(_with_client(go))


if __name__ == "__main__":
    main()
