"""Lab-owned 15-minute bar history for the intraday mover study.

Kept in its own DuckDB file (data/m14_m18/intraday_history/<market>.duckdb), never in the
production candle store: a long backfill must not hold the lock the live session and the
after-close pipeline need. Resumable - a code already covered back to the requested start
is skipped - so an interrupted run simply continues. Every INDstocks call goes through the
client's rate limiter (broker/indstocks/ratelimit.py) like any other caller.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk_lab.artifacts import OUTPUT

ROOT = OUTPUT / "intraday_history"
BATCH_CODES = 25  # codes per flush to disk (each fetch still sends at most 5 per call)
LIQUID_NOTIONAL = 2e7


def db_path(market: str) -> Path:
    return ROOT / f"{market}.duckdb"


def _connect(market: str, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path(market)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only and path.exists())
    if not read_only:
        con.execute(
            "CREATE TABLE IF NOT EXISTS m15 (code VARCHAR, ts BIGINT, open DOUBLE, high DOUBLE, "
            "low DOUBLE, close DOUBLE, volume DOUBLE, PRIMARY KEY (code, ts))"
        )
    return con


def liquid_codes(market: str, sessions: int = 250, min_share: float = 0.2) -> list[str]:
    """Codes that met the movers screen's liquidity floor on at least `min_share` of the
    last `sessions` sessions - the universe the intraday study can actually trade."""

    from tradedesk.potential_movers import _read_daily

    frame, _ = _read_daily(market, None)
    frame = frame.sort_values(["scrip_code", "ts"])
    frame["notional"] = (
        (frame["volume"] * frame["close"])
        .groupby(frame["scrip_code"])
        .transform(lambda s: s.rolling(20, min_periods=15).mean())
    )
    last = sorted(frame["ts"].unique())[-sessions:]
    recent = frame[frame["ts"].isin(last)]
    share = recent.groupby("scrip_code")["notional"].apply(lambda s: (s >= LIQUID_NOTIONAL).mean())
    return sorted(str(c) for c in share[share >= min_share].index)


def covered_from(market: str) -> dict[str, int]:
    con = _connect(market)
    try:
        return dict(con.execute("SELECT code, min(ts) FROM m15 GROUP BY code").fetchall())
    finally:
        con.close()


@dataclass(frozen=True)
class Bar:
    """A completed 15-minute bar with exact (float) volume. CoinDCX's base-asset volume is
    fractional and the shared Candle model rounds it, so crypto history and crypto live
    features both use these instead - the two paths must see identical numbers."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def bars_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[Bar]:
    """CoinDCX candle rows ({time ms, open, high, low, close, volume}) -> Bars."""

    return [
        Bar(
            ts=datetime.fromtimestamp(int(r["time"]) / 1000, tz=UTC),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"]),
        )
        for r in rows
    ]


def store(market: str, candles: Mapping[str, Sequence[Any]]) -> int:
    rows = [
        (code, int(c.ts.timestamp()), c.open, c.high, c.low, c.close, float(c.volume))
        for code, items in candles.items()
        for c in items
    ]
    if not rows:
        return 0
    con = _connect(market)
    try:
        frame = pd.DataFrame(
            rows, columns=["code", "ts", "open", "high", "low", "close", "volume"]
        ).drop_duplicates(["code", "ts"])
        con.register("incoming", frame)
        con.execute("INSERT OR REPLACE INTO m15 SELECT * FROM incoming")
        con.unregister("incoming")
    finally:
        con.close()
    return len(rows)


async def backfill(
    market: str,
    days: int,
    codes: Sequence[str] | None = None,
    *,
    stop_at: datetime | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Fetch [now - days, now). By default only codes not yet covered back to the start
    (resumable backfill); `refresh` re-fetches the window for every code (nightly update -
    INSERT OR REPLACE keeps it idempotent)."""

    if market == "crypto":
        return await backfill_crypto(days, codes, stop_at=stop_at, refresh=refresh)
    from tradedesk.cli import _with_client

    now = datetime.now(IST)
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    wanted = list(codes or liquid_codes(market))
    have = {} if refresh else covered_from(market)
    todo = [
        c for c in wanted if have.get(c, 1 << 62) > int((start + timedelta(days=3)).timestamp())
    ]
    summary: dict[str, Any] = {
        "market": market,
        "wanted": len(wanted),
        "todo": len(todo),
        "stored": 0,
    }

    async def run(client: Any) -> None:
        for i in range(0, len(todo), BATCH_CODES):
            if stop_at and datetime.now(IST) >= stop_at:
                summary["stopped_early"] = True
                break
            batch = todo[i : i + BATCH_CODES]
            got = await client.candles_history(Interval.M15, batch, start, now, skip_invalid=True)
            summary["stored"] += store(market, got)
            summary["done"] = i + len(batch)
            print(json.dumps({"progress": summary["done"], "of": len(todo)}), flush=True)
        summary["transport"] = vars(client.stats)

    await _with_client(run)
    return summary


async def backfill_crypto(
    days: int,
    codes: Sequence[str] | None = None,
    *,
    stop_at: datetime | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Every ACTIVE CoinDCX INR pair (the live master list, so a delisted pair simply drops
    out), exact-volume rows, one pair at a time under the client's own rate limiter."""

    from tradedesk.broker.coindcx import CoinDcxClient
    from tradedesk.markets.crypto_universe import active_crypto_codes

    now = datetime.now(IST)
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    summary: dict[str, Any] = {"market": "crypto", "stored": 0}
    async with CoinDcxClient() as client:
        instruments = await client.instruments()
        active = set(active_crypto_codes(instruments))
        pairs = {i.scrip_code: str(i.custom_symbol) for i in instruments if i.scrip_code in active}
        client.register_pairs(pairs)
        wanted = [c for c in (codes or sorted(pairs)) if c in pairs]
        have = {} if refresh else covered_from("crypto")
        cutoff = int((start + timedelta(days=3)).timestamp())
        todo = [c for c in wanted if have.get(c, 1 << 62) > cutoff]
        summary.update({"wanted": len(wanted), "todo": len(todo)})
        for i in range(0, len(todo), 10):
            if stop_at and datetime.now(IST) >= stop_at:
                summary["stopped_early"] = True
                break
            batch = todo[i : i + 10]
            rows = await client.candle_rows_history(Interval.M15, batch, start, now)
            summary["stored"] += store("crypto", {c: bars_from_rows(r) for c, r in rows.items()})
            summary["done"] = i + len(batch)
            print(json.dumps({"progress": summary["done"], "of": len(todo)}), flush=True)
    return summary


def load_bars(market: str, start: datetime, end: datetime) -> pd.DataFrame:
    """All stored bars in [start, end), with IST session date and bar start time."""

    con = _connect(market, read_only=True)
    try:
        frame = con.execute(
            "SELECT * FROM m15 WHERE ts >= ? AND ts < ? ORDER BY code, ts",
            [int(start.timestamp()), int(end.timestamp())],
        ).df()
    finally:
        con.close()
    stamps = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    frame["session"] = stamps.dt.date
    frame["hhmm"] = stamps.dt.strftime("%H:%M")
    return frame


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradedesk_lab.intraday_history")
    parser.add_argument("command", choices=["backfill", "status"])
    parser.add_argument("--market", choices=["nse", "bse", "crypto"], default="nse")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--stop-at", help="HH:MM IST - stop cleanly before this (e.g. 08:45)")
    args = parser.parse_args(argv)
    if args.command == "status":
        have = covered_from(args.market)
        print(json.dumps({"codes": len(have)}))
        return
    stop_at = None
    if args.stop_at:
        h, m = (int(x) for x in args.stop_at.split(":"))
        now = datetime.now(IST)
        stop_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if stop_at <= now:
            stop_at += timedelta(days=1)
    print(json.dumps(asyncio.run(backfill(args.market, args.days, stop_at=stop_at)), default=str))


if __name__ == "__main__":
    main()
