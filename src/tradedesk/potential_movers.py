"""Potential-movers watch: stocks whose next session is unusually likely to move >= 5%.

Research only. It lists names that history says are about twice as likely as the base rate to
close >= +5% the next session. It does NOT say which direction the move goes, never sizes or
places anything, and has no live authority. A first event study (BSE, ~2M stock-days) found the
conditions below raise the chance of a +5% day about 2x while the mean next-day return stays
~0 - they predict volatility, not direction - so this list is a "look here" screen whose
forward hit rate is tracked honestly against the base rate before anything else is built on it.

Everything is causal: a row is described using data up to its own close, the tier rates shown
beside each name only use rows whose next session had already happened, and each day's list is
written once and never edited (resolution is stored separately).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.intraday_contract_race import DB_PATHS, _atomic_json
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "potential-movers-v1"
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/potential_movers")
MOVE = 0.05  # the "big move" the list is about
MIN_HISTORY_BARS = 45  # new listings are some of the biggest movers; the model sees their age
MIN_NOTIONAL = {"nse": 2e7, "bse": 2e7, "crypto": 0.0}  # 20-day average traded value
LIST_SIZE = 25
MIN_TIER_SAMPLES = 200  # a tier with less history than this is shown but not ranked on it

# Fixed a priori from the first study; changing a definition means a new VERSION.
TIERS: dict[str, str] = {
    "capitulation": "6m-range bottom quarter, >5% under EMA50, down >=8% in 5 sessions",
    "volatile_deep": "6m-range bottom quarter, >5% under EMA50, last range >1.5x normal",
    "deep": "6m-range bottom quarter, >5% under EMA50",
}
TIER_ORDER = ("capitulation", "volatile_deep", "deep")


def _read_daily(market: str, db_path: Path | None) -> tuple[pd.DataFrame, dict[str, str]]:
    import duckdb

    from tradedesk.leader_discovery import _connect_read_only

    path = db_path or DB_PATHS[market]
    connection = _connect_read_only(path)
    try:
        frame = connection.execute(
            "SELECT scrip_code, ts, open, high, low, close, volume FROM candles "
            "WHERE interval='1day' ORDER BY scrip_code, ts"
        ).df()
        try:
            names = dict(
                connection.execute(
                    "SELECT scrip_code, trading_symbol FROM instruments"
                ).fetchall()
            )
        except duckdb.Error:
            names = {}
    finally:
        connection.close()
    return frame, names


def read_results_dates(market: str, db_path: Path | None = None) -> dict[str, list[date]]:
    """scrip_code -> sorted results-announcement dates (equities only; empty for crypto)."""

    if market == "crypto":
        return {}
    import duckdb

    from tradedesk.leader_discovery import _connect_read_only

    connection = _connect_read_only(db_path or DB_PATHS[market])
    try:
        rows = connection.execute(
            "SELECT i.scrip_code, r.event_date FROM results_events r "
            "JOIN instruments i ON i.trading_symbol = r.symbol "
            "WHERE r.purpose ILIKE '%result%'"
        ).fetchall()
    except duckdb.Error:
        return {}
    finally:
        connection.close()
    out: dict[str, set[date]] = {}
    for code, when in rows:
        out.setdefault(str(code), set()).add(pd.Timestamp(when).date())
    return {code: sorted(days) for code, days in out.items()}


def _next_business_day(day: date) -> date:
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _results_features(
    dates: list[date], close: np.ndarray, events: list[date]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """results due today / by the next session, and the stock's own past reaction size.

    The reaction of an event is its larger absolute close move over the event day and the
    next session; it is only used for rows after that reaction is complete (no look-ahead)."""

    index = {d: i for i, d in enumerate(dates)}
    event_set = set(events)
    today = np.array([float(d in event_set) for d in dates])
    nxt = np.array([float(_next_business_day(d) in event_set) for d in dates])
    reactions: list[tuple[int, float]] = []  # (row index when it becomes known, |move|)
    for event in events:
        i = index.get(event)
        if i is None or i < 1 or i + 1 >= len(dates):
            continue
        move = max(abs(close[i] / close[i - 1] - 1), abs(close[i + 1] / close[i] - 1))
        reactions.append((i + 2, float(move)))
    reactions.sort()
    hist = np.full(len(dates), np.nan)
    total, count, k = 0.0, 0, 0
    for row in range(len(dates)):
        while k < len(reactions) and reactions[k][0] <= row:
            total += reactions[k][1]
            count += 1
            k += 1
        if count:
            hist[row] = total / count
    return today, nxt, hist


def build_panel(
    frame: pd.DataFrame, market: str, results: dict[str, list[date]] | None = None
) -> pd.DataFrame:
    """One row per (stock, session) with causal features and the realised next session."""

    parts: list[pd.DataFrame] = []
    for code, group in frame.groupby("scrip_code", sort=False):
        g = group.drop_duplicates("ts").reset_index(drop=True)
        if len(g) < MIN_HISTORY_BARS:
            continue
        close = g["close"].astype(float)
        rng = (g["high"] - g["low"]).astype(float) / close
        high6 = close.rolling(126, min_periods=40).max()
        low6 = close.rolling(126, min_periods=40).min()
        row_dates = (
            pd.to_datetime(g["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
        )
        res_today, res_next, hist_res = _results_features(
            list(row_dates), close.to_numpy(), (results or {}).get(str(code), [])
        )
        part = pd.DataFrame(
            {
                "code": code,
                "res_today": res_today,
                "res_next": res_next,
                "hist_res_move": hist_res,
                "date": pd.to_datetime(g["ts"], unit="s", utc=True)
                .dt.tz_convert("Asia/Kolkata")
                .dt.date,
                "close": close,
                "below50": close / close.ewm(span=50, adjust=False).mean() - 1,
                "pos6m": (close - low6) / (high6 - low6).replace(0, np.nan),
                "d5": close / close.shift(5) - 1,
                "ret1": close.pct_change(),
                "ret20": close / close.shift(20) - 1,
                "age_bars": np.arange(1, len(g) + 1, dtype=float),
                "gap": g["open"].astype(float) / close.shift(1) - 1,
                "close_loc": (close - g["low"]) / (g["high"] - g["low"]).replace(0, np.nan),
                "atr_pct": rng.rolling(14, min_periods=10).mean(),
                "dist_hi20": close / g["high"].rolling(20, min_periods=15).max().shift(1) - 1,
                "rng_rel": rng / rng.rolling(20, min_periods=15).median(),
                "vol_rel": g["volume"] / g["volume"].rolling(20, min_periods=15).mean(),
                "notional": (g["volume"] * close).rolling(20, min_periods=15).mean(),
                "next_ret": close.shift(-1) / close - 1,
                "next_high": g["high"].shift(-1) / close - 1,
                "next_open_to_close": close.shift(-1) / g["open"].astype(float).shift(-1) - 1,
            }
        )
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    panel = pd.concat(parts, ignore_index=True)
    panel["hist_res_move"] = panel["hist_res_move"].fillna(0.0)
    panel = panel.replace([np.inf, -np.inf], np.nan)
    panel = panel.dropna(subset=["below50", "pos6m", "d5", "rng_rel", "notional"])
    panel = panel[panel["notional"] >= MIN_NOTIONAL[market]]
    deep = (panel["below50"] < -0.05) & (panel["pos6m"] < 0.25)
    tier = pd.Series("", index=panel.index, dtype=object)
    tier[deep] = "deep"
    tier[deep & (panel["rng_rel"] > 1.5)] = "volatile_deep"
    tier[deep & (panel["d5"] < -0.08)] = "capitulation"
    panel["tier"] = tier
    return panel.reset_index(drop=True)


def _wilson_low(successes: int, n: int) -> float | None:
    if n == 0:
        return None
    z, p = 1.96, successes / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((centre - margin) / (1 + z * z / n))


def _rates(rows: pd.DataFrame) -> dict[str, Any]:
    n = int(len(rows))
    if n == 0:
        return {"n": 0}
    up = int((rows["next_ret"] >= MOVE).sum())
    down = int((rows["next_ret"] <= -MOVE).sum())
    return {
        "n": n,
        "p_up5": up / n,
        "p_up5_wilson_low": _wilson_low(up, n),
        "p_down5": down / n,
        "mean_next_return": float(rows["next_ret"].mean()),
        "median_next_return": float(rows["next_ret"].median()),
    }


def tier_statistics(panel: pd.DataFrame, as_of: date) -> dict[str, Any]:
    """Rates from sessions whose NEXT session is strictly before `as_of` (already known)."""

    known = panel[panel["next_ret"].notna() & (panel["date"] < as_of)]
    base = _rates(known)
    stats: dict[str, Any] = {"base": base}
    for name in TIER_ORDER:
        # a capitulation row is also deep; tiers are nested, so count by membership
        mask = {
            "capitulation": known["tier"] == "capitulation",
            "volatile_deep": known["tier"].isin(["capitulation", "volatile_deep"]),
            "deep": known["tier"] != "",
        }[name]
        rates = _rates(known[mask])
        if rates["n"] and base.get("p_up5"):
            rates["lift_up5"] = rates["p_up5"] / base["p_up5"]
        stats[name] = rates
    return stats


def current_list(
    panel: pd.DataFrame,
    names: Mapping[str, str],
    as_of: date,
    stats: Mapping[str, Any],
    model: Any | None = None,
) -> list[dict[str, Any]]:
    """Today's names. With a validated model every liquid stock is ranked by its probability;
    without one the fixed tier rules decide (and only tiered stocks can appear)."""

    today = panel[panel["date"] == as_of].copy()
    if model is not None:
        from tradedesk.potential_movers_model import score_rows

        today = today.dropna(subset=["vol_rel", "ret1", "ret20", "gap", "close_loc", "atr_pct"])
        today["model_probability"] = score_rows(model, today)
        today = today.sort_values("model_probability", ascending=False)
    else:
        today = today[today["tier"] != ""]
        today["tier_rank"] = today["tier"].map({t: i for i, t in enumerate(TIER_ORDER)})
        today["depth"] = -today["below50"]
        today = today.sort_values(["tier_rank", "depth"], ascending=[True, False])
    if today.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, r in today.head(LIST_SIZE).iterrows():
        tier_stats = stats.get(str(r["tier"]), {}) if r["tier"] else {}
        rows.append(
            {
                "scrip_code": str(r["code"]),
                "symbol": names.get(str(r["code"]), str(r["code"])),
                "tier": str(r["tier"]),
                "close": round(float(r["close"]), 2),
                "below_ema50_pct": round(float(r["below50"]) * 100, 1),
                "five_session_change_pct": round(float(r["d5"]) * 100, 1),
                "range_vs_normal": round(float(r["rng_rel"]), 2),
                "volume_vs_20d": round(float(r["vol_rel"]), 2),
                "model_probability": (
                    round(float(r["model_probability"]), 4) if "model_probability" in r else None
                ),
                "historical_p_up5": tier_stats.get("p_up5"),
                "historical_lift": tier_stats.get("lift_up5"),
                "enough_history": bool(tier_stats.get("n", 0) >= MIN_TIER_SAMPLES),
            }
        )
    return rows


CRYPTO_LIST_WINDOW = timedelta(hours=2)  # the target candle opens when the last one closes


def candle_end(market: str, session: date) -> datetime:
    """When the daily candle dated `session` is complete."""

    if market == "crypto":
        return datetime.combine(session + timedelta(days=1), time(5, 30), tzinfo=IST)
    return datetime.combine(session, time(15, 30), tzinfo=IST)


def forward_valid(market: str, session: date, now: datetime) -> bool:
    """A list only counts as a forecast if the session is complete and the next one has not
    (equities: written before the next open; crypto: within two hours of the new candle)."""

    if now < candle_end(market, session):
        return False
    if market == "crypto":
        return now < candle_end(market, session) + CRYPTO_LIST_WINDOW
    nxt = session + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return now < datetime.combine(nxt, time(9, 15), tzinfo=IST)


def drop_incomplete(panel: pd.DataFrame, market: str, now: datetime) -> pd.DataFrame:
    """Never describe a stock with a candle that is still forming."""

    if panel.empty:
        return panel
    latest = max(panel["date"])
    return panel[panel["date"] < latest] if now < candle_end(market, latest) else panel


def results_watch(
    panel: pd.DataFrame, names: Mapping[str, str], session: date, results: Mapping[str, list[date]]
) -> dict[str, Any]:
    """Stocks reporting results today (after the close) or by the next session.

    Such days move a lot - and the size is predictable from the stock's own history - but
    nothing here predicts direction, so these are names to watch, never calls."""

    nxt = _next_business_day(session)
    today = panel[panel["date"] == session]
    due = today[(today["res_today"] > 0) | (today["res_next"] > 0)].copy()
    # how often a results day actually produced a >=5% move, by the stock's own history
    past = panel[
        (panel["res_today"] > 0) & panel["next_ret"].notna() & (panel["date"] < session)
    ]
    move = np.maximum(past["ret1"].abs(), past["next_ret"].abs())
    big = past["hist_res_move"] >= 0.04
    stats = {
        "events": int(len(past)),
        "p_move5_any": float((move >= MOVE).mean()) if len(past) else None,
        "p_move5_if_usually_big": float((move[big] >= MOVE).mean()) if big.any() else None,
        "p_move5_if_usually_small": float((move[~big] >= MOVE).mean()) if (~big).any() else None,
    }
    due = due.sort_values("hist_res_move", ascending=False)
    rows = []
    for _, r in due.head(LIST_SIZE).iterrows():
        code = str(r["code"])
        upcoming = [d for d in results.get(code, []) if d >= session]
        rows.append(
            {
                "scrip_code": code,
                "symbol": names.get(code, code),
                "results_date": (upcoming[0] if upcoming else nxt).isoformat(),
                "usual_results_move_pct": round(float(r["hist_res_move"]) * 100, 1),
                "close": round(float(r["close"]), 2),
            }
        )
    covers = max((d for days in results.values() for d in days), default=None)
    return {
        "calendar_covers_to": covers.isoformat() if covers else None,
        "calendar_current": bool(covers and covers >= session),
        "historical_rates": stats,
        "candidates": rows,
    }


def sessions_behind(market: str, latest: date, now: datetime) -> int:
    """Weekdays between the newest stored session and the one that should exist by now."""

    if market == "crypto":
        return max(0, (now.date() - latest).days - 1)
    closed_today = now >= candle_end(market, now.date())
    expected = now.date() if closed_today else now.date() - timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    behind, day = 0, latest
    while day < expected:
        day += timedelta(days=1)
        behind += day.weekday() < 5
    return behind


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def resolve_forward(
    panel: pd.DataFrame, entries: list[dict[str, Any]], base_by_date: Mapping[str, float]
) -> dict[str, Any]:
    """Score previously written lists against what the next session actually did."""

    by_key = {(str(r.code), r.date): r for r in panel.itertuples()}
    picks = ups = downs = 0
    total_ret = 0.0
    base_total = 0.0
    sessions = 0
    for entry in entries:
        as_of = date.fromisoformat(entry["as_of"])
        scored = 0
        for item in entry["list"]:
            row = by_key.get((item["scrip_code"], as_of))
            if row is None or row.next_ret != row.next_ret:  # next session not yet known
                continue
            scored += 1
            picks += 1
            total_ret += float(row.next_ret)
            ups += int(row.next_ret >= MOVE)
            downs += int(row.next_ret <= -MOVE)
        if scored:
            sessions += 1
            base_total += base_by_date.get(entry["as_of"], 0.0) * scored
    if picks == 0:
        return {"sessions": 0, "picks": 0}
    base_up = base_total / picks
    return {
        "sessions": sessions,
        "picks": picks,
        "up5_rate": ups / picks,
        "down5_rate": downs / picks,
        "mean_next_return": total_ret / picks,
        "same_day_universe_up5_rate": base_up,
        "lift_over_universe": (ups / picks) / base_up if base_up else None,
        "up5_wilson_low": _wilson_low(ups, picks),
    }


def run_potential_movers(
    market: str,
    *,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    as_of: date | None = None,
    write_ledger: bool = True,
) -> dict[str, Any]:
    """Build today's list from the latest completed daily bar and refresh forward scoring."""

    if market not in MIN_NOTIONAL:
        raise ValueError(f"unsupported market {market!r}")
    frame, names = _read_daily(market, db_path)
    results = read_results_dates(market, db_path)
    panel = drop_incomplete(build_panel(frame, market, results), market, datetime.now(IST))
    if panel.empty:
        return {"market": market, "status": "no_data", "version": VERSION}
    latest = max(panel["date"])
    session = as_of or latest
    stats = tier_statistics(panel, session)
    root = output_root / market
    model, model_report = None, {"adopted": False, "reason": "preview: rules only (no look-ahead)"}
    if as_of is None:
        from tradedesk.potential_movers_model import load_or_train

        model, model_report = load_or_train(panel, root, session)
    listing = current_list(panel, names, session, stats, model)
    ledger = root / "ledger.jsonl"
    entries = _read_ledger(ledger)
    now = datetime.now(IST)
    on_time = forward_valid(market, session, now)
    if (
        write_ledger
        and as_of is None
        and on_time
        and not any(e["as_of"] == session.isoformat() for e in entries)
    ):
        record = {
            "version": VERSION,
            "market": market,
            "as_of": session.isoformat(),
            "generated_at": datetime.now(IST).isoformat(),
            "tier_definitions": TIERS,
            "list": listing,
        }
        record["sha256"] = canonical_sha256(record)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        entries.append(record)

    # same-day universe base rate (all liquid stocks that session) for an honest comparison
    known = panel[panel["next_ret"].notna()]
    by_date = known.groupby("date")["next_ret"].apply(lambda s: float((s >= MOVE).mean()))
    base_by_date = {d.isoformat(): v for d, v in by_date.items()}
    forward = resolve_forward(panel, entries, base_by_date)

    report = {
        "version": VERSION,
        "market": market,
        "status": "available" if listing else "no_candidates",
        "authority": "research_watch_only",
        "eligible_for_live": False,
        "direction": "unknown - this list predicts a larger move, not its sign",
        "detail": (
            "Names ranked by how likely they are to close >=+5% next session (see "
            "model_evaluation for the out-of-sample hit rate vs the all-stock base rate). "
            "Much of such a move happens at the open, and open-to-close returns of the picks "
            "were about zero out of sample, so this is a screen for names to watch, not a "
            "trade: no entry, stop or size, and direction is not guaranteed."
        ),
        "as_of": session.isoformat(),
        "recorded_as_forecast": bool(on_time),
        "sessions_behind": sessions_behind(market, latest, now),
        "data_stale": sessions_behind(market, latest, now) > 0,
        "results_watch": results_watch(panel, names, session, results) if results else None,
        "latest_data_session": latest.isoformat(),
        "move_threshold": MOVE,
        "rank_method": "model" if model is not None else "tier_rules",
        "model_evaluation": model_report,
        "tier_definitions": TIERS,
        "tier_statistics_in_sample": stats,
        "candidates": listing,
        "forward_tracking": forward,
        "generated_at": datetime.now(IST).isoformat(),
    }
    if write_ledger and as_of is None:
        _atomic_json(root / "latest.json", report)
    return report


def load_potential_movers(
    market: str, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    path = output_root / market / "latest.json"
    if not path.exists():
        return {"market": market, "status": "not_collected", "version": VERSION}
    return dict(json.loads(path.read_text(encoding="utf-8")))
