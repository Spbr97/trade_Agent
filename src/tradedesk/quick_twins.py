"""Quick twins: does a setup's ENTRY pick winners? Answered in about a week, not months.

Owner-approved 2026-10-18. The trend-following setups hold for weeks, so their live verdicts
are slow. Each live call (signal tracker, source "live") gets a twin with the same stock and
day but a fixed, mechanical exit: buy the next session's OPEN, sell the CLOSE five sessions
later. The twin is compared with the average stock in the same market bought and sold at the
same moments ("beat market"), after the market's round-trip cost. Twins never alert and never
size anything; they are measurement only, written to data/reports/quick_twins_<market>.jsonl.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST

DATABASES = {
    "nse": Path("data/tradedesk.duckdb"),
    "bse": Path("data/bse.duckdb"),
    "crypto": Path("data/crypto.duckdb"),
}
TRACKERS = {
    "nse": Path("data/reports/nse_signal_tracking.jsonl"),
    "bse": Path("data/reports/bse_signal_tracking.jsonl"),
    "crypto": Path("data/reports/crypto_signal_tracking.jsonl"),
}
HOLD_SESSIONS = 5
LOOKAHEAD_DAYS = 16  # calendar days that always contain six sessions, holidays included


def output_path(market: str) -> Path:
    return Path(f"data/reports/quick_twins_{market}.jsonl")


def _connect(db: Path, tries: int = 40, delay_s: float = 15.0) -> Any:
    import duckdb

    for attempt in range(tries):
        try:
            return duckdb.connect(str(db), read_only=True)
        except duckdb.IOException:
            if attempt == tries - 1:
                raise
            time.sleep(delay_s)
    raise RuntimeError("unreachable")


def _bars(con: Any, start: date) -> pd.DataFrame:
    begin = datetime.combine(start, datetime.min.time(), tzinfo=IST)
    end = begin + timedelta(days=LOOKAHEAD_DAYS)
    frame = con.execute(
        "SELECT scrip_code, ts, open, close FROM candles WHERE interval = '1day' "
        "AND ts >= ? AND ts < ?",
        [int(begin.timestamp()), int(end.timestamp())],
    ).df()
    frame["day"] = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    return frame.sort_values(["scrip_code", "day"])


def window_returns(bars: pd.DataFrame, entry_day: date) -> dict[str, float]:
    """Open of `entry_day` -> close HOLD_SESSIONS sessions later, per code that traded on
    every one of those sessions."""

    out: dict[str, float] = {}
    for code, rows in bars.groupby("scrip_code"):
        rows = rows[rows["day"] >= entry_day]
        if len(rows) <= HOLD_SESSIONS or rows["day"].iloc[0] != entry_day:
            continue
        entry = float(rows["open"].iloc[0])
        if entry > 0:
            out[str(code)] = float(rows["close"].iloc[HOLD_SESSIONS]) / entry - 1
    return out


def next_session(bars: pd.DataFrame, armed_on: date) -> date | None:
    later = sorted(d for d in bars["day"].unique() if d > armed_on)
    return later[0] if later else None


def update(market: str, *, root: Path = Path("."), round_trip: float | None = None) -> int:
    """Resolve every live call's twin whose five sessions have passed. Returns how many new
    twins were written. Idempotent: a call already twinned is skipped."""

    from tradedesk.digests import round_trip as market_round_trip

    cost = market_round_trip(market, root=root) if round_trip is None else round_trip
    out_path = root / output_path(market)
    done = {row.get("signal_id") for row in _read(out_path)}
    calls = [
        row for row in _read(root / TRACKERS[market])
        if row.get("source") == "live" and row.get("armed_on") and row.get("signal_id") not in done
    ]  # fmt: skip
    if not calls:
        return 0
    by_day: dict[date, list[dict[str, Any]]] = {}
    for row in calls:
        by_day.setdefault(date.fromisoformat(str(row["armed_on"])), []).append(row)
    written: list[dict[str, Any]] = []
    con = _connect(root / DATABASES[market])
    try:
        for armed_on, rows in sorted(by_day.items()):
            bars = _bars(con, armed_on)
            entry_day = next_session(bars, armed_on)
            if entry_day is None:
                continue
            everyone = window_returns(bars, entry_day)
            if not everyone:
                continue  # five sessions have not passed yet
            market_mean = sum(everyone.values()) / len(everyone)
            for row in rows:
                ret = everyone.get(str(row.get("scrip_code")))
                if ret is None:
                    continue
                written.append(
                    {
                        "signal_id": row.get("signal_id"),
                        "setup": row.get("setup"),
                        "symbol": row.get("symbol"),
                        "market": market,
                        "armed_on": armed_on.isoformat(),
                        "entry_on": entry_day.isoformat(),
                        "net_return": ret - cost,
                        "market_return": market_mean,
                        "beat_market": ret > market_mean,
                        "won": ret - cost > 0,
                    }
                )
    finally:
        con.close()
    if written:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as stream:
            for row in written:
                stream.write(json.dumps(row) + "\n")
    return len(written)


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def summary(market: str, *, root: Path = Path(".")) -> dict[str, dict[str, Any]]:
    """setup -> {twins, winners, beat_market, mean_net_return} for the dashboard."""

    out: dict[str, dict[str, Any]] = {}
    for row in _read(root / output_path(market)):
        s = out.setdefault(
            str(row.get("setup")), {"twins": 0, "winners": 0, "beat_market": 0, "_sum": 0.0}
        )
        s["twins"] += 1
        s["winners"] += int(bool(row.get("won")))
        s["beat_market"] += int(bool(row.get("beat_market")))
        s["_sum"] += float(row.get("net_return") or 0.0)
    for s in out.values():
        s["mean_net_return"] = s.pop("_sum") / s["twins"] if s["twins"] else None
    return out


def summaries(markets: Iterable[str], *, root: Path = Path(".")) -> Mapping[str, Any]:
    return {m: summary(m, root=root) for m in markets}
