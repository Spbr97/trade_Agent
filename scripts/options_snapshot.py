"""Options-chain data collection (ML training plan, Phase 0.3, 2026-09-13): NIFTY's option
chain carries real, currently-unused signal - implied volatility and put/call open interest -
but INDstocks has no HISTORICAL option-chain endpoint, only a live/current one. That means
there is no way to ever backfill this data; the only way to have it later is to start
capturing it today. This script does exactly that and nothing more - it does not wire
anything into prediction/features.py yet (see the comment in
prediction/train.py::market_context() for why: every already-labeled historical signal
predates this log, so joining it now would just be None for 100% of training rows).

Deliberately stores only two derived numbers per run, not the full raw per-strike payload -
there's no historical endpoint to backfill against regardless, so the summary is what a
future feature would actually consume.

Run once daily on weekdays (tradedesk-options-snapshot scheduled task). Log:
data/reports/options_snapshot.jsonl, one row appended per run.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from tradedesk.broker.indstocks import IndstocksClient, TokenProvider
from tradedesk.broker.indstocks.models import IST
from tradedesk.broker.indstocks.rest import BASE_URL

LOG = Path("data/reports/options_snapshot.jsonl")
UNDERLYING = "NIFTY"
UNDERLYING_SCRIP = "40000001"  # NSE_40000001's numeric SECURITY_ID, the same NIFTY 50 index
STRIKE_COUNT = 10


def _atm_iv(strikes: dict[str, Any], underlying_ltp: float) -> float | None:
    """Average of the nearest-ATM strike's CE/PE `iv` fields."""
    if not strikes:
        return None
    nearest = min(strikes, key=lambda k: abs(float(k) - underlying_ltp))
    leg = strikes[nearest]
    ivs = [leg[side]["iv"] for side in ("ce", "pe") if leg.get(side) and leg[side].get("iv") is not None]  # noqa: E501
    return sum(ivs) / len(ivs) if ivs else None


def _put_call_oi_ratio(strikes: dict[str, Any]) -> float | None:
    """Sum of PE open interest over sum of CE open interest, across every returned strike."""
    call_oi = sum(leg["ce"]["oi"] for leg in strikes.values() if leg.get("ce"))
    put_oi = sum(leg["pe"]["oi"] for leg in strikes.values() if leg.get("pe"))
    return put_oi / call_oi if call_oi else None


async def snapshot() -> dict[str, Any] | None:
    http = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
    client = IndstocksClient(TokenProvider(http=http))
    try:
        expiries = await client.list_expiries(UNDERLYING)
        if not expiries:
            print(f"no upcoming expiries for {UNDERLYING}; aborting")
            return None
        nearest_expiry = expiries[0]  # ascending order per the API doc
        chain = await client.option_chain(
            exchange="NSE",
            segment="INDEX",
            underlying_scrip=UNDERLYING_SCRIP,
            expiry=nearest_expiry,
            strike_count=STRIKE_COUNT,
        )
        underlying_ltp = float(chain["underlying_ltp"])
        strikes = chain.get("strikes") or {}
        return {
            "date": datetime.now(IST).date().isoformat(),
            "underlying": UNDERLYING,
            "expiry": nearest_expiry,
            "underlying_ltp": underlying_ltp,
            "atm_iv": _atm_iv(strikes, underlying_ltp),
            "put_call_oi_ratio": _put_call_oi_ratio(strikes),
        }
    finally:
        await client.aclose()


def main() -> None:
    row = asyncio.run(snapshot())
    if row is None:
        return
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(
        f"{row['date']} {row['underlying']} (expiry {row['expiry']}): "
        f"ltp={row['underlying_ltp']}, atm_iv={row['atm_iv']}, "
        f"put_call_oi_ratio={row['put_call_oi_ratio']}"
    )
    print(f"appended to {LOG}")


if __name__ == "__main__":
    main()
