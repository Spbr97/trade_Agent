"""Crypto signal tracker (M13 follow-up): "test a few coins and track to see if you're
right" - crypto trades 24/7, but paper/book.py::PaperBook is deliberately NSE-only
(CLAUDE.md M13 note: Portfolio.costs would need CryptoCostModel wiring through the paper
book's sizing too, and risk/sizing.py's whole-unit assumption already breaks on BTC/ETH -
see docs/signoff-crypto-phase4.md). Rather than force crypto through that, this is a
lighter, honest tool: log every real signal the crypto scan produces on a fixed watchlist
of liquid pairs, then later grade each one with the SAME triple-barrier rule the
prediction layer trains on (prediction/labeling.py) - hit target before stop, or not,
gap-aware. It answers "was the setup right" without needing position sizing, a cost
model wired into a portfolio, or fractional quantities at all.

Deliberately logs EVERY signal the engine detects (`wl.entries`), not just
`wl.active`: a first real run showed every BTC/ETH/BNB signal rejected purely on "size
0" (NSE's reused Rs 250/trade risk budget can't afford qty=1 at BTC's price - see
docs/signoff-crypto-phase4.md, a money/sizing question, not a "was the pattern right"
question). `rejected_for` is kept on each row so tradeability is still visible
separately from whether the setup itself called the move correctly.

Run daily (scheduled via Task Scheduler, crypto_daily task) - crypto's daily candle is a
UTC-midnight bar, settled well before this runs at 07:00 IST.

Log: data/reports/crypto_signal_tracking.jsonl, one row per signal, updated in place as
outcomes resolve (rewrite-the-file style, small enough not to need anything fancier).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradedesk.backtest.runner import prepare_market
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.history_loader import default_start, load_history
from tradedesk.engine.signals import Signal
from tradedesk.markets import crypto_market
from tradedesk.prediction.labeling import triple_barrier
from tradedesk.scan import build_watchlist, scan_config

DB = Path("data/crypto.duckdb")
LOG = Path("data/reports/crypto_signal_tracking.jsonl")
WATCHLIST = [
    "CDX_BTCINR", "CDX_ETHINR", "CDX_SOLINR", "CDX_XRPINR", "CDX_DOGEINR",
    "CDX_ADAINR", "CDX_TRXINR", "CDX_XLMINR", "CDX_HBARINR", "CDX_BNBINR",
]  # fmt: skip
MAX_HOLD = 10  # sessions -> calendar days for a 24/7 market; matches config/risk.yaml


@dataclass
class TrackedSignal:
    signal_id: str
    scrip_code: str
    symbol: str
    setup: str
    grade: str
    armed_on: str
    entry: float
    stop: float
    t1: float
    t2: float
    net_rr_t1: float | None
    net_rr_t2: float | None
    rejected_for: list[str]
    logged_at: str
    outcome: str | None = None  # None until resolved: "target" | "stop" | "gap_stop" | "timeout"
    label: int | None = None
    exit_price: float | None = None
    r_multiple: float | None = None
    resolved_at: str | None = None


def load_log() -> dict[str, TrackedSignal]:
    if not LOG.exists():
        return {}
    rows = [
        json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {r["signal_id"]: TrackedSignal(**r) for r in rows}


def save_log(rows: dict[str, TrackedSignal]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as fh:
        for r in rows.values():
            fh.write(json.dumps(asdict(r)) + "\n")


def resolve_outcomes(store: CandleStore, rows: dict[str, TrackedSignal]) -> int:
    """Grade every unresolved signal against real price history since it armed, using the
    exact triple-barrier rule prediction/labeling.py trains on."""
    resolved = 0
    for row in rows.values():
        if row.outcome is not None:
            continue
        df = store.load(row.scrip_code, Interval.D1, adjusted=False)
        armed = date.fromisoformat(row.armed_on)
        entry_dates = [d for d in df.index.date if d > armed]
        if not entry_dates:
            continue
        entry_date = entry_dates[0]
        start_idx = list(df.index.date).index(entry_date)
        lab = triple_barrier(
            df.iloc[start_idx:], entry=row.entry, stop=row.stop, target=row.t1,
            max_hold=MAX_HOLD,
        )  # fmt: skip
        if lab.outcome == "insufficient":
            continue  # not enough history yet - try again tomorrow
        row.outcome = lab.outcome
        row.label = lab.label
        row.exit_price = lab.exit_price
        if lab.exit_price is not None:
            row.r_multiple = (lab.exit_price - row.entry) / (row.entry - row.stop)
        row.resolved_at = datetime.now(IST).isoformat()
        resolved += 1
    return resolved


def _line(label: str, done: list[TrackedSignal]) -> str:
    if not done:
        return f"  {label}: 0 resolved yet"
    wins = sum(1 for r in done if r.label == 1)
    avg_r = sum(r.r_multiple for r in done if r.r_multiple is not None) / len(done)
    return f"  {label}: {wins}/{len(done)} hit T1 ({wins / len(done):.0%}), avg R {avg_r:+.2f}"


def scoreboard(rows: dict[str, TrackedSignal]) -> str:
    """Split by whether the signal would actually have been tradeable (net R:R/sizing),
    since most crypto signals fail that for reasons unrelated to pattern quality - see
    the module docstring."""
    done = [r for r in rows.values() if r.outcome is not None]
    tradeable = [r for r in done if not r.rejected_for]
    untradeable = [r for r in done if r.rejected_for]
    return (
        f"{len(rows)} logged, {len(done)} resolved:\n"
        + _line("would have been tradeable", tradeable)
        + "\n"
        + _line("rejected (sizing/net R:R/etc)", untradeable)
    )


def main() -> None:
    settings = load_config(".")
    market = crypto_market(settings)
    with CandleStore(DB) as store:
        # incremental load, watchlist coins only - keeps this fast and independent of
        # the full 338-pair Task Scheduler load
        import asyncio

        from tradedesk.broker.coindcx import CoinDcxClient
        from tradedesk.broker.coindcx.rest import CoinDcxError

        start = default_start(Interval.D1, datetime.now(IST))

        async def refresh() -> None:
            async with CoinDcxClient() as c:
                c.register_pairs(store.custom_symbols(WATCHLIST))
                await load_history(
                    c, store, WATCHLIST, Interval.D1, start=start,
                    max_codes_per_call=1, error_types=(CoinDcxError,),
                )  # fmt: skip

        asyncio.run(refresh())

        today = store.last_ts(WATCHLIST[0], Interval.D1)
        if today is None:
            print("no data for the watchlist; aborting")
            return
        day = today.date()

        cfg = scan_config(settings, day, market=market)
        md = prepare_market(store, WATCHLIST, WATCHLIST[0], cfg)
        wl = build_watchlist(md, cfg, settings, day, market=market)

        rows = load_log()
        new = 0
        for e in wl.entries:  # every detected signal, not just wl.active - see module docstring
            sig: Signal = e.signal
            if sig.id in rows:
                continue
            rows[sig.id] = TrackedSignal(
                signal_id=sig.id, scrip_code=sig.scrip_code, symbol=sig.symbol,
                setup=sig.setup.value, grade=e.grade.value, armed_on=sig.armed_on.isoformat(),
                entry=sig.trigger, stop=sig.stop, t1=sig.t1, t2=sig.t2,
                net_rr_t1=e.net_rr_t1, net_rr_t2=e.net_rr_t2, rejected_for=list(e.rejected_for),
                logged_at=datetime.now(IST).isoformat(),
            )  # fmt: skip
            new += 1

        resolved = resolve_outcomes(store, rows)
        save_log(rows)
        print(
            f"{day}: {len(wl.entries)} signals detected "
            f"({len(wl.active)} would be tradeable, {new} new today), {resolved} newly resolved"
        )
        print(scoreboard(rows))


if __name__ == "__main__":
    main()
