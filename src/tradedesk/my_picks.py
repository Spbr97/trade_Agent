"""Your own picks, scored fairly: time-stamped by the server when you log them, priced from
the first 15-minute bar that STARTS after that moment, and compared with every other liquid
stock bought at that same bar.

Why it is built this way: a chart always looks obvious afterwards. The only fair test of a
human read is a prediction written down before the move, scored against what a random pick
at the same moment would have done.

- Logging: `add_pick` stamps `logged_at` from the server clock (never from the request),
  appends to a hash-chained JSONL so an entry cannot be back-dated or edited unnoticed, and
  counts one pick per stock per direction per session (a second tap is refused).
- Prices: the equity pre-takeoff collector already fetches completed 15-minute bars for the
  whole liquid universe every 15 minutes; `write_intraday_bars` keeps that session's bars
  (`<root>/<market>/intraday_bars/<date>.json`). A pick outside that universe cannot be
  priced intraday and is reported as such rather than scored on a looser basis.
- Score: reference = OPEN of the first bar starting at or after `logged_at` (a pre-market
  pick therefore uses the 09:15 open; a pick after the last bar uses the next session).
  Outcomes = that session's last close and the next session's last close. Baseline = the
  same return for every other stock in the file from the same bar.

Advisory only: nothing here creates a signal, an alert or an order.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "my-picks-v1"
PICKS_PATH = Path("data/reports/my_picks.jsonl")
BARS_ROOT = Path("data/m14_m18/pre_takeoff_watch")
MARKETS = ("nse", "bse")
DIRECTIONS = ("up", "down")
MAX_NOTE_CHARS = 280
KEEP_BAR_DAYS = 45
SESSION_FINAL_AT = time(15, 30)  # a bars file written at/after this holds the whole session
MIN_PICKS_TO_JUDGE = 30

Resolver = Callable[[str, str], tuple[str, str] | None]
_WRITE_LOCK = threading.Lock()  # one writer at a time keeps the hash chain linear


class PickError(ValueError):
    """A pick that cannot be accepted; the message is safe to show the user."""


# --------------------------------------------------------------------------- logging


def resolve_symbol(market: str, symbol: str) -> tuple[str, str] | None:
    """(scrip_code, trading_symbol) for an equity on that exchange, or None."""

    from tradedesk.analysis import db_for
    from tradedesk.leader_discovery import _connect_read_only

    db = db_for(market)
    if not db.exists():
        return None
    con = _connect_read_only(db)
    try:
        row = con.execute(
            "SELECT scrip_code, trading_symbol FROM instruments "
            "WHERE kind = 'equity' AND exch = ? AND upper(trading_symbol) = ? "
            "ORDER BY (series = 'EQ') DESC, scrip_code LIMIT 1",
            ["BSE" if market == "bse" else "NSE", symbol.strip().upper()],
        ).fetchone()
    finally:
        con.close()
    return (str(row[0]), str(row[1])) if row else None


def load_picks(path: Path = PICKS_PATH) -> tuple[list[dict[str, Any]], bool]:
    """All picks in logging order, and whether the hash chain is intact."""

    if not path.exists():
        return [], True
    rows: list[dict[str, Any]] = []
    intact = True
    previous = "GENESIS"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        body = {k: v for k, v in row.items() if k != "sha256"}
        if row.get("previous_sha256") != previous or canonical_sha256(body) != row.get("sha256"):
            intact = False
        previous = str(row.get("sha256"))
        rows.append(row)
    return rows, intact


def add_pick(
    market: str,
    symbol: str,
    direction: str,
    note: str = "",
    *,
    now: datetime | None = None,
    path: Path = PICKS_PATH,
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    market = (market or "").strip().lower()
    direction = (direction or "").strip().lower()
    if market not in MARKETS:
        raise PickError("market must be nse or bse")
    if direction not in DIRECTIONS:
        raise PickError("direction must be up or down")
    if not symbol or not symbol.strip():
        raise PickError("symbol is required")
    note = (note or "").strip()
    if len(note) > MAX_NOTE_CHARS:
        raise PickError(f"note is limited to {MAX_NOTE_CHARS} characters")
    resolved = (resolver or resolve_symbol)(market, symbol)
    if resolved is None:
        raise PickError(f"{symbol.strip().upper()} is not a listed {market.upper()} equity")
    code, trading_symbol = resolved
    with _WRITE_LOCK:
        return _append(path, market, code, trading_symbol, direction, note, now)


def _append(
    path: Path,
    market: str,
    code: str,
    trading_symbol: str,
    direction: str,
    note: str,
    now: datetime | None,
) -> dict[str, Any]:
    logged_at = (now or datetime.now(IST)).astimezone(IST)
    rows, _ = load_picks(path)
    for row in rows:
        if (
            row["market"] == market
            and row["scrip_code"] == code
            and row["direction"] == direction
            and str(row["logged_at"])[:10] == logged_at.date().isoformat()
        ):
            raise PickError(
                f"{trading_symbol} {direction} is already logged for today "
                f"(at {str(row['logged_at'])[11:16]}); only the first pick counts"
            )
    record: dict[str, Any] = {
        "version": VERSION,
        "logged_at": logged_at.isoformat(),
        "market": market,
        "scrip_code": code,
        "symbol": trading_symbol,
        "direction": direction,
        "note": note,
        "previous_sha256": str(rows[-1]["sha256"]) if rows else "GENESIS",
    }
    record["sha256"] = canonical_sha256(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    return record


# ------------------------------------------------------------------- intraday bars


def write_intraday_bars(
    market: str,
    candles: Mapping[str, Sequence[Candle]],
    *,
    observed_at: datetime,
    output_root: Path = BARS_ROOT,
) -> Path | None:
    """Keep this session's completed 15-minute bars for every collected stock. Called by
    the equity pre-takeoff collector; overwrites the session's file each slot, so the last
    slot after the close leaves the full day."""

    session = observed_at.astimezone(IST).date()
    bars: dict[str, list[list[Any]]] = {}
    for code, rows in candles.items():
        kept = [
            [c.ts.astimezone(IST).strftime("%H:%M"), c.open, c.high, c.low, c.close]
            for c in rows
            if isinstance(c, Candle)
            and c.interval is Interval.M15
            and c.ts.astimezone(IST).date() == session
            and c.close_time <= observed_at
        ]
        if kept:
            bars[code] = sorted(kept)
    if not bars:
        return None
    directory = output_root / market / "intraday_bars"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session.isoformat()}.json"
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "market": market,
                "session": session.isoformat(),
                "written_at": observed_at.astimezone(IST).isoformat(),
                "bar_minutes": 15,
                "bars": bars,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    cutoff = session - timedelta(days=KEEP_BAR_DAYS)
    for old in directory.glob("*.json"):
        try:
            if date.fromisoformat(old.stem) < cutoff:
                old.unlink()
        except ValueError:
            continue
    return path


def load_sessions(market: str, root: Path = BARS_ROOT) -> dict[date, dict[str, Any]]:
    directory = root / market / "intraday_bars"
    out: dict[date, dict[str, Any]] = {}
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            out[date.fromisoformat(path.stem)] = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
    return out


# ------------------------------------------------------------------------ scoring


def _final(day: date, data: Mapping[str, Any]) -> bool:
    written = datetime.fromisoformat(str(data.get("written_at")))
    return written >= datetime.combine(day, SESSION_FINAL_AT, tzinfo=IST)


def _start(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=IST)


def _signed(value: float, direction: str) -> float:
    return value if direction == "up" else -value


def _universe(
    data: Mapping[str, Any], hhmm: str, exclude: str, direction: str
) -> list[float]:
    out = []
    for code, bars in data["bars"].items():
        if code == exclude:
            continue
        ref = next((b for b in bars if b[0] == hhmm), None)
        if ref and ref[1] > 0:
            out.append(_signed(bars[-1][4] / ref[1] - 1, direction))
    return out


def score_pick(
    pick: Mapping[str, Any], sessions: Mapping[date, Mapping[str, Any]]
) -> dict[str, Any]:
    keys = ("logged_at", "market", "symbol", "direction", "note")
    out: dict[str, Any] = {k: pick[k] for k in keys}
    logged = datetime.fromisoformat(str(pick["logged_at"])).astimezone(IST)
    code = str(pick["scrip_code"])
    days = sorted(d for d in sessions if d >= logged.date())
    reference = None
    for day in days:
        data = sessions[day]
        if code not in data["bars"]:
            out["status"] = "no_intraday_data"
            out["detail"] = "not in the collected liquid universe for that session"
            return out
        after = [b for b in data["bars"][code] if _start(day, b[0]) >= logged]
        if after:
            reference = (day, after[0])
            break
        if not _final(day, data):
            break  # the next bar has not completed yet
    if reference is None:
        out["status"] = "waiting"
        out["detail"] = "the first bar after your pick has not completed yet"
        return out

    day, ref_bar = reference
    data = sessions[day]
    ref_price = float(ref_bar[1])
    bars = data["bars"][code]
    same = _signed(bars[-1][4] / ref_price - 1, pick["direction"])
    universe = _universe(data, ref_bar[0], code, pick["direction"])
    out.update(
        {
            "status": "scored" if _final(day, data) else "live",
            "session": day.isoformat(),
            "reference_bar": ref_bar[0],
            "reference_price": ref_price,
            "same_day_return": same,  # in your direction: positive = you were right
            "universe_same_day_mean": sum(universe) / len(universe) if universe else None,
            "universe_size": len(universe),
            "percentile": (
                (sum(u < same for u in universe) + 0.5 * sum(u == same for u in universe))
                / len(universe)
                if universe
                else None
            ),
            "universe_right_share": (
                sum(u > 0 for u in universe) / len(universe) if universe else None
            ),
            "universe_5pct_share": (
                sum(u >= 0.05 for u in universe) / len(universe) if universe else None
            ),
        }
    )
    later = [d for d in sorted(sessions) if d > day and _final(d, sessions[d])]
    if later:
        nxt = sessions[later[0]]["bars"].get(code)
        if nxt:
            out["next_session"] = later[0].isoformat()
            out["next_day_return"] = _signed(nxt[-1][4] / ref_price - 1, pick["direction"])
    return out


def summarise(scored: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    final = [s for s in scored if s.get("status") == "scored" and s.get("percentile") is not None]
    n = len(final)
    if not n:
        return {"scored": 0, "verdict": "no scored picks yet"}

    def mean(key: str) -> float | None:
        values = [float(s[key]) for s in final if s.get(key) is not None]
        return sum(values) / len(values) if values else None

    mean_pct = mean("percentile") or 0.0
    # Under "no skill" each percentile is uniform(0, 1): mean 0.5, sd sqrt(1/12).
    z = (mean_pct - 0.5) / (math.sqrt(1 / 12) / math.sqrt(n))
    if n < MIN_PICKS_TO_JUDGE:
        verdict = f"too few picks to judge ({n} of {MIN_PICKS_TO_JUDGE})"
    elif z >= 2:
        verdict = "your picks are beating random stocks at the same moment"
    elif z <= -2:
        verdict = "your picks are doing worse than random stocks at the same moment"
    else:
        verdict = "no evidence yet that your picks beat random stocks"
    return {
        "scored": n,
        "mean_return_your_direction": mean("same_day_return"),
        "random_stock_mean_same_moment": mean("universe_same_day_mean"),
        "mean_percentile": mean_pct,
        "z_vs_random": z,
        "right_direction_rate": sum(float(s["same_day_return"]) > 0 for s in final) / n,
        "random_right_direction_rate": mean("universe_right_share"),
        "five_pct_rate": sum(float(s["same_day_return"]) >= 0.05 for s in final) / n,
        "random_five_pct_rate": mean("universe_5pct_share"),
        "next_day_mean_return": mean("next_day_return"),
        "verdict": verdict,
        "minimum_to_judge": MIN_PICKS_TO_JUDGE,
    }


def picks_report(
    *, path: Path = PICKS_PATH, bars_root: Path = BARS_ROOT, market: str | None = None
) -> dict[str, Any]:
    picks, intact = load_picks(path)
    if market in MARKETS:
        picks = [p for p in picks if p["market"] == market]
    sessions = {m: load_sessions(m, bars_root) for m in MARKETS}
    scored = [score_pick(p, sessions[p["market"]]) for p in picks]
    return {
        "version": VERSION,
        "chain_intact": intact,
        "picks": list(reversed(scored)),
        "summary": summarise(scored),
        "authority": "advisory_only",
    }
