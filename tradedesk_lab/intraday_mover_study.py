"""Intraday mover study: does the first hour (or two) of a session say which stocks keep
running for the rest of the day - after costs, against every other stock at that moment?

Why: the daily mover study found ~47 NSE stocks close +5% on a typical day and that ~93% of
the median mover's gain comes AFTER the open, yet nothing known at the previous close picks
them. The next place an edge could live is the session itself.

Decision points (completed 15-minute bars at decision -> entry bar):
  first_hour       4 bars (09:15-10:15) -> decide on the 10:16 collector run -> enter at the
                   OPEN of the 10:30 bar (the 10:15 bar has already started by then)
  first_two_hours  8 bars (09:15-11:15) -> decide 11:16 -> enter at the 11:30 open
Features use only bars before the decision: gap, move since open, last bar's move, range so
far vs the stock's normal day range, volume so far vs its normal volume for the same bars,
position in the range, distance from the session high, share of up bars, prior 5-session
return. Normals are rolling means/medians of PRIOR sessions only.
Targets: rest_of_day (entry -> close, intraday costs) and to_next_close (entry -> next
session close, delivery costs). The search grammar, same-moment stock null, day-clustered t,
chronological 70/30 split and Bonferroni bar are mover_study.search's, unchanged.

Forward: a survivor is activated in a hash-chained registry; the equity pre-takeoff
collector (which already fetches every stock's completed bars every 15 minutes) calls
`register_live` and records picks inside the decision window only - before the entry bar
starts. The nightly run scores them; a forward-cleared candidate becomes one review_queue
item. Research only: nothing here alerts, sizes or trades.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.models import TradeType
from tradedesk_lab import intraday_history
from tradedesk_lab import mover_study as daily
from tradedesk_lab.artifacts import OUTPUT

VERSION = "intraday-mover-study-v1"
OUTPUT_ROOT = OUTPUT / "intraday_mover_study"
# completed bars at decision; the opening checkpoints decide on the 09:31 / 09:46 collector runs
CHECKPOINTS = {"opening_bar": 1, "opening_two_bars": 2, "first_hour": 4, "first_two_hours": 8}
FEATURES = (
    "gap", "r_open", "r_lastbar", "rng_rel", "vol_rel",
    "close_loc", "dist_high", "up_share", "ret5",
)  # fmt: skip
TARGETS: dict[str, TradeType] = {
    "rest_of_day": TradeType.INTRADAY,
    "to_next_close": TradeType.DELIVERY,
    # same-day short from the entry bar to the close (intraday only: CLAUDE.md allows no
    # overnight shorts); research only, like every target here
    "short_rest_of_day": TradeType.INTRADAY,
}
REFERENCE_SESSIONS = 20


@dataclass(frozen=True)
class Clock:
    """How a market's 15-minute bars map onto sessions."""

    offset_seconds: int  # added to epoch seconds -> the session clock
    first_minute: int  # bar 0 starts this many minutes after that clock's midnight
    last_index: int  # index of the session's final bar
    min_bars: int  # a session with fewer bars is incomplete and skipped
    tz: tzinfo  # session dates are calendar days in this zone


CLOCKS = {
    "nse": Clock(19_800, 9 * 60 + 15, 24, 24, IST),  # 09:15 .. the 15:15 bar
    "bse": Clock(19_800, 9 * 60 + 15, 24, 24, IST),
    # one CoinDCX daily bar = one UTC day (05:30 IST), 96 bars; a few missing bars allowed
    "crypto": Clock(0, 0, 95, 90, UTC),
}
LIQUID_NOTIONAL = {"nse": 2e7, "bse": 2e7, "crypto": 5e5}  # avg daily traded value, INR
SHORTABLE = frozenset({"nse", "bse"})  # CoinDCX spot cannot be shorted
CONSENSUS_TARGET = "short_rest_of_day"
CONSENSUS_N = 3  # per checkpoint; top 3 held up best on days the ranking had not seen


def targets_for(market: str) -> dict[str, TradeType]:
    return {
        name: trade
        for name, trade in TARGETS.items()
        if market in SHORTABLE or not name.startswith("short_")
    }


def bar_start(session: date, index: int, market: str = "nse") -> datetime:
    clock = CLOCKS[market]
    midnight = datetime.combine(session, time(0), tzinfo=clock.tz)
    return midnight + timedelta(minutes=clock.first_minute + 15 * index)


# --------------------------------------------------------------------- session frame


def _aggregate_sql(source: str, clock: Clock) -> str:
    parts = [
        "code",
        f"(ts + {clock.offset_seconds}) // 86400 AS dayn",
        "count(*) AS n_bars",
        "max(CASE WHEN idx = 0 THEN open END) AS open0",
        f"max(CASE WHEN idx = {clock.last_index} THEN close END) AS close_last",
        "max(high) AS hi_day",
        "min(low) AS lo_day",
        "sum(volume) AS v_day",
    ]
    for k in sorted(set(CHECKPOINTS.values())):
        parts += [
            f"max(high) FILTER (WHERE idx < {k}) AS hi_{k}",
            f"min(low) FILTER (WHERE idx < {k}) AS lo_{k}",
            f"max(CASE WHEN idx = {k - 1} THEN close END) AS c_{k}",
            f"max(CASE WHEN idx = {k - 1} THEN open END) AS o_{k}",
            f"sum(volume) FILTER (WHERE idx < {k}) AS v_{k}",
            f"avg(CASE WHEN close > open THEN 1.0 ELSE 0.0 END) FILTER (WHERE idx < {k}) AS up_{k}",
            f"max(CASE WHEN idx = {k + 1} THEN open END) AS entry_{k}",
        ]
    indexed = (
        f"SELECT *, ((((ts + {clock.offset_seconds}) % 86400) // 60) - {clock.first_minute}) // 15 "
        f"AS idx FROM {source}"
    )
    return f"SELECT {', '.join(parts)} FROM ({indexed}) GROUP BY 1, 2 ORDER BY 1, 2"


def session_aggregates(bars: pd.DataFrame, market: str = "nse") -> pd.DataFrame:
    """Per (code, session) aggregates from raw m15 rows (code, ts, open, high, low, close,
    volume) - computed in DuckDB so a year of the whole universe stays fast."""

    import duckdb

    con = duckdb.connect()
    try:
        con.register("bars", bars[["code", "ts", "open", "high", "low", "close", "volume"]])
        out = con.execute(_aggregate_sql("bars", CLOCKS[market])).df()
    finally:
        con.close()
    out["date"] = [date(1970, 1, 1) + timedelta(days=int(d)) for d in out.pop("dayn")]
    return out


def stored_session_aggregates(market: str, start: datetime, end: datetime) -> pd.DataFrame:
    """The same aggregates, computed inside the lab history database (a year of the whole
    universe is ~10M bars - never pulled into memory raw)."""

    import duckdb

    con = duckdb.connect(str(intraday_history.db_path(market)), read_only=True)
    try:
        source = (
            f"(SELECT * FROM m15 WHERE ts >= {int(start.timestamp())} "
            f"AND ts < {int(end.timestamp())}) AS stored"
        )
        out = con.execute(_aggregate_sql(source, CLOCKS[market])).df()
    finally:
        con.close()
    out["date"] = [date(1970, 1, 1) + timedelta(days=int(d)) for d in out.pop("dayn")]
    return out


def add_features(sessions: pd.DataFrame, market: str = "nse") -> dict[str, pd.DataFrame]:
    """One frame per checkpoint: features from bars before the decision, references from
    PRIOR sessions only, and the tradeable targets."""

    s = sessions.sort_values(["code", "date"]).reset_index(drop=True)
    s = s[s["n_bars"] >= CLOCKS[market].min_bars].copy()
    g = s.groupby("code", sort=False)
    s["prev_close"] = g["close_last"].shift(1)
    s["ret5"] = s["prev_close"] / g["close_last"].shift(6) - 1
    s["day_range"] = (s["hi_day"] - s["lo_day"]) / s["open0"]
    s["rng_ref"] = g["day_range"].transform(
        lambda x: x.rolling(REFERENCE_SESSIONS, min_periods=10).median().shift(1)
    )
    s["notional_ref"] = (
        (s["v_day"] * s["close_last"])
        .groupby(s["code"])
        .transform(lambda x: x.rolling(REFERENCE_SESSIONS, min_periods=10).mean().shift(1))
    )
    s["next_close"] = g["close_last"].shift(-1)
    s["gap"] = s["open0"] / s["prev_close"] - 1
    frames = {}
    for name, k in CHECKPOINTS.items():
        f = s.copy()
        f["vol_ref"] = g[f"v_{k}"].transform(
            lambda x: x.rolling(REFERENCE_SESSIONS, min_periods=10).mean().shift(1)
        )
        f["r_open"] = f[f"c_{k}"] / f["open0"] - 1
        f["r_lastbar"] = f[f"c_{k}"] / f[f"o_{k}"] - 1
        f["rng_rel"] = ((f[f"hi_{k}"] - f[f"lo_{k}"]) / f["open0"]) / f["rng_ref"]
        f["vol_rel"] = f[f"v_{k}"] / f["vol_ref"]
        span = (f[f"hi_{k}"] - f[f"lo_{k}"]).replace(0, np.nan)
        f["close_loc"] = (f[f"c_{k}"] - f[f"lo_{k}"]) / span
        f["dist_high"] = f[f"c_{k}"] / f[f"hi_{k}"] - 1
        f["up_share"] = f[f"up_{k}"]
        f["rest_of_day"] = f["close_last"] / f[f"entry_{k}"] - 1
        f["to_next_close"] = f["next_close"] / f[f"entry_{k}"] - 1
        f["short_rest_of_day"] = f[f"entry_{k}"] / f["close_last"] - 1
        f = f[f["notional_ref"] >= LIQUID_NOTIONAL[market]]
        frames[name] = f.replace([np.inf, -np.inf], np.nan)
    return frames


# --------------------------------------------------------------------------- search


def costs_for(market: str) -> dict[str, float]:
    return {name: daily.cost_fraction(market, trade) for name, trade in targets_for(market).items()}


def search(
    frames: Mapping[str, pd.DataFrame], costs: Mapping[str, float], market: str = "nse"
) -> dict[str, Any]:
    """Every checkpoint is one family: the Bonferroni bar covers the train survivors of ALL
    checkpoints together, so adding decision times cannot quietly multiply false finds."""

    grammar = daily.conditions(FEATURES, results_flag=False)
    trained = {
        checkpoint: daily.train_phase(
            frame, costs, grammar=grammar, features=FEATURES, targets=tuple(targets_for(market))
        )
        for checkpoint, frame in frames.items()
    }
    family = sum(len(t.get("passed", [])) for t in trained.values())
    bar = daily.family_bar(family)
    out: dict[str, Any] = {
        "version": VERSION,
        "checkpoints": {},
        "survivors": [],
        "family_passed_train": family,
        "validation_bar_t": bar,
    }
    for checkpoint, t in trained.items():
        result = daily.validate_phase(t, costs, bar)
        for survivor in result.get("survivors", []):
            survivor["name"] = f"{checkpoint}:{survivor['name']}"
            survivor["checkpoint"] = checkpoint
            out["survivors"].append(survivor)
        for near in result.get("near_misses", []):
            near["name"] = f"{checkpoint}:{near['name']}"
        out["checkpoints"][checkpoint] = {
            k: result.get(k)
            for k in (
                "status", "sessions", "train", "validation_from", "validation_to",
                "tested", "passed_train", "near_misses",
            )
        }  # fmt: skip
    out["tested"] = sum((c.get("tested") or 0) for c in out["checkpoints"].values())
    out["costs"] = dict(costs)
    return out


def run_search(market: str, *, root: Path | None = None, days: int = 400) -> dict[str, Any]:
    now = datetime.now(IST)
    root = root or OUTPUT_ROOT / market
    frames = add_features(
        stored_session_aggregates(market, now - timedelta(days=days), now), market
    )
    result = search(frames, costs_for(market), market)
    result["searched_at"] = now.isoformat()
    result["activated"] = daily.activate(root, result, now=now)
    daily._write(root / "search_latest.json", result)
    return result


# ---------------------------------------------------------------------------- forward


def reference_values(market: str, as_of: date, *, bars: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-code references for a live decision on `as_of`, from sessions BEFORE it."""

    clock = CLOCKS[market]
    if bars is None:  # live path: aggregate inside the history database (seconds, not minutes)
        start = datetime.combine(as_of - timedelta(days=45), time(0), tzinfo=clock.tz)
        end = datetime.combine(as_of, time(0), tzinfo=clock.tz)
        s = stored_session_aggregates(market, start, end)
    else:
        s = session_aggregates(bars, market)
    s = s[(s["n_bars"] >= clock.min_bars) & (s["date"] < as_of)].sort_values(["code", "date"])
    rows = []
    for code, g in s.groupby("code", sort=False):
        tail = g.tail(REFERENCE_SESSIONS)
        if len(tail) < 10:
            continue
        closes = g["close_last"].to_numpy()
        row = {
            "code": code,
            "prev_close": float(closes[-1]),
            "ret5": float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else np.nan,
            "rng_ref": float(((tail["hi_day"] - tail["lo_day"]) / tail["open0"]).median()),
            "notional_ref": float((tail["v_day"] * tail["close_last"]).mean()),
        }
        for k in set(CHECKPOINTS.values()):
            row[f"vol_ref_{k}"] = float(tail[f"v_{k}"].mean())
        rows.append(row)
    return pd.DataFrame(rows)


def cached_reference_values(market: str, as_of: date, root: Path) -> pd.DataFrame:
    """References change once a day, so the first decision window of a session computes
    them and the later windows (and the second collector market) reuse the file."""

    path = root / "refs" / f"{as_of.isoformat()}.json"
    if path.exists():
        try:
            return pd.read_json(path, orient="records")
        except ValueError:
            pass
    refs = reference_values(market, as_of)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    refs.to_json(temporary, orient="records", double_precision=15)
    temporary.replace(path)
    for old in path.parent.glob("*.json"):
        if old.stem < (as_of - timedelta(days=10)).isoformat():
            old.unlink(missing_ok=True)
    return refs


def live_features(
    candles: Mapping[str, Sequence[Any]],
    refs: pd.DataFrame,
    session: date,
    k: int,
    market: str = "nse",
) -> pd.DataFrame:
    """Checkpoint-k features for today from completed bars 0..k-1 only. `candles` holds
    Candle objects (equity) or intraday_history.Bar (crypto, exact volume) - anything with
    ts/open/high/low/close/volume."""

    clock = CLOCKS[market]
    ref = refs.set_index("code")
    rows = []
    for code, items in candles.items():
        if code not in ref.index or not isinstance(items, (list, tuple)):
            continue
        today = sorted(
            (c for c in items if hasattr(c, "ts")
             and getattr(c, "interval", Interval.M15) is Interval.M15
             and c.ts.astimezone(clock.tz).date() == session),
            key=lambda c: c.ts,
        )  # fmt: skip
        first = [c for c in today if c.ts < bar_start(session, k, market)]
        if len(first) < k or first[0].ts != bar_start(session, 0, market):
            continue
        r = ref.loc[code]
        hi, lo = max(c.high for c in first), min(c.low for c in first)
        last = first[-1]
        open0 = first[0].open
        rows.append(
            {
                "code": code,
                "date": session,
                "gap": open0 / r["prev_close"] - 1,
                "r_open": last.close / open0 - 1,
                "r_lastbar": last.close / last.open - 1,
                "rng_rel": ((hi - lo) / open0) / r["rng_ref"] if r["rng_ref"] else np.nan,
                "vol_rel": sum(c.volume for c in first) / r[f"vol_ref_{k}"]
                if r[f"vol_ref_{k}"]
                else np.nan,
                "close_loc": (last.close - lo) / (hi - lo) if hi > lo else np.nan,
                "dist_high": last.close / hi - 1,
                "up_share": float(np.mean([c.close > c.open for c in first])),
                "ret5": r["ret5"],
                "notional_ref": r["notional_ref"],
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame[frame["notional_ref"] >= LIQUID_NOTIONAL[market]]
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame.dropna(subset=list(FEATURES))


def register_live(
    market: str,
    candles: Mapping[str, Sequence[Any]],
    *,
    now: datetime,
    root: Path | None = None,
    refs: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Called by the equity and crypto pre-takeoff collectors. Registers each active
    candidate's picks only inside its decision window [bar k completes, entry bar starts)."""

    root = root or OUTPUT_ROOT / market
    candidates = daily.active_candidates(root)
    if not candidates:
        return {"status": "no_active_candidates"}
    now = now.astimezone(IST)
    session = now.astimezone(CLOCKS[market].tz).date()
    path = root / "forward_picks.jsonl"
    done = {(r["candidate"], r["target"], r["decision_session"]) for r in daily.read_chain(path)[0]}
    registered = consensus = 0
    for name, k in CHECKPOINTS.items():
        opens, entry_at = bar_start(session, k, market), bar_start(session, k + 1, market)
        mine = [c for c in candidates if c.get("checkpoint") == name]
        if not mine or not (opens <= now < entry_at):
            continue
        if refs is None:
            refs = cached_reference_values(market, session, root)
        today = live_features(candles, refs, session, k, market)
        if today.empty:
            continue
        ranked = daily.add_ranks(today, FEATURES)
        for candidate in mine:
            key = (candidate["name"], candidate["target"], session.isoformat())
            if key in done:
                continue
            condition = tuple(daily.Clause(**c) for c in candidate["clauses"])
            codes = sorted(str(c) for c in ranked.loc[daily.mask_for(ranked, condition), "code"])
            daily.append_chain(
                path,
                {
                    "event": "picks_registered",
                    "candidate": candidate["name"],
                    "target": candidate["target"],
                    "checkpoint": name,
                    "decision_session": session.isoformat(),
                    "registered_at": now.isoformat(),
                    "deadline": entry_at.isoformat(),
                    "entry_bar": entry_at.astimezone(IST).strftime("%H:%M"),
                    "codes": codes,
                },
            )
            registered += 1
        consensus += _register_consensus(path, done, mine, ranked, name, session, now, entry_at)
    return {"status": "checked", "registered": registered, "consensus_registered": consensus}


def _register_consensus(
    path: Path,
    done: set[tuple[str, str, str]],
    mine: Sequence[Mapping[str, Any]],
    ranked: pd.DataFrame,
    checkpoint: str,
    session: date,
    now: datetime,
    entry_at: datetime,
) -> int:
    """The few stocks the most active short rules agree on. Overnight research (15 Oct 2026)
    found the individual rules flag ~500 of ~1,000 stocks a day, so most of their edge was
    market drift; ranking by agreement was the part that held up on days it had not seen."""

    shorts = [c for c in mine if c["target"] == CONSENSUS_TARGET]
    name = f"consensus_top{CONSENSUS_N}:{checkpoint}"
    if not shorts or (name, CONSENSUS_TARGET, session.isoformat()) in done:
        return 0
    votes = pd.Series(0, index=ranked.index)
    for candidate in shorts:
        condition = tuple(daily.Clause(**c) for c in candidate["clauses"])
        votes += daily.mask_for(ranked, condition).astype(int)
    pool = ranked.assign(votes=votes)
    pool = pool[pool["votes"] > 0].sort_values(
        ["votes", "notional_ref", "code"], ascending=[False, False, True]
    )
    top = pool.head(CONSENSUS_N)
    if top.empty:
        return 0
    daily.append_chain(
        path,
        {
            "event": "picks_registered",
            "candidate": name,
            "target": CONSENSUS_TARGET,
            "checkpoint": checkpoint,
            "decision_session": session.isoformat(),
            "registered_at": now.isoformat(),
            "deadline": entry_at.isoformat(),
            "entry_bar": entry_at.astimezone(IST).strftime("%H:%M"),
            "codes": [str(c) for c in top["code"]],
            "votes": {str(c): int(v) for c, v in zip(top["code"], top["votes"], strict=True)},
            "rules_voting": len(shorts),
        },
    )
    return 1


def forward_scores(
    root: Path, frames: Mapping[str, pd.DataFrame], costs: Mapping[str, float]
) -> list[dict[str, Any]]:
    """Score live picks registered before their entry bar, against every liquid stock
    entered at the same bar of the same session."""

    rows, intact = daily.read_chain(root / "forward_picks.jsonl")
    values: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in rows:
        if row.get("event") != "picks_registered" or not row["codes"]:
            continue
        if datetime.fromisoformat(row["registered_at"]) >= datetime.fromisoformat(row["deadline"]):
            continue
        frame = frames.get(row["checkpoint"])
        if frame is None:
            continue
        day = frame[frame["date"] == date.fromisoformat(row["decision_session"])].set_index("code")
        target = row["target"]
        if day.empty or day[target].dropna().empty:
            continue
        picked = day.reindex(row["codes"])[target].dropna()
        if picked.empty:
            continue
        values.setdefault((row["candidate"], target), []).append(
            (float(picked.mean()), float(picked.mean() - day[target].mean()))
        )
    out = []
    for (name, target), pairs in values.items():
        gross = np.array([p[0] for p in pairs])
        excess = np.array([p[1] for p in pairs])
        n = len(pairs)
        sd = excess.std(ddof=1) if n > 1 else 0.0
        t = float(excess.mean() / (sd / np.sqrt(n))) if n > 1 and sd > 0 else 0.0
        net = float(gross.mean() - costs[target])
        out.append(
            {
                "candidate": name,
                "target": target,
                "forward_days": n,
                "mean_net": net,
                "mean_excess": float(excess.mean()),
                "t_excess": t,
                "cleared_forward_bar": bool(
                    n >= daily.FORWARD_MIN_DAYS and net > 0 and t >= daily.FORWARD_MIN_T
                ),
                "chain_intact": intact,
            }
        )
    return out


def pick_record(
    root: Path, frames: Mapping[str, pd.DataFrame], costs: Mapping[str, float]
) -> dict[str, Any]:
    """Winners out of picks, stock by stock: the consensus top 3 and the per-rule picks
    (each stock counted once per session and checkpoint), overall and for the latest
    session. A win is a pick that made money after costs; "beat market" is a pick that did
    better than the average stock entered at the same bar, so a falling market cannot flatter
    a short on its own. Descriptive only."""

    rows, _ = daily.read_chain(root / "forward_picks.jsonl")
    Pick = dict[tuple[str, str, str], tuple[float, float]]
    seen: dict[str, Pick] = {"consensus": {}, "rules": {}}
    for row in rows:
        if row.get("event") != "picks_registered" or not row["codes"]:
            continue
        if datetime.fromisoformat(row["registered_at"]) >= datetime.fromisoformat(row["deadline"]):
            continue
        frame = frames.get(row["checkpoint"])
        if frame is None:
            continue
        target = row["target"]
        day = frame[frame["date"] == date.fromisoformat(row["decision_session"])].set_index("code")
        if day.empty or day[target].dropna().empty:
            continue
        mean = float(day[target].mean())
        group = "consensus" if str(row["candidate"]).startswith("consensus") else "rules"
        for code, value in day.reindex(row["codes"])[target].dropna().items():
            key = (row["decision_session"], row["checkpoint"], str(code))
            seen[group].setdefault(key, (float(value) - costs[target], float(value) - mean))

    def tally(items: list[tuple[float, float]]) -> dict[str, Any]:
        n = len(items)
        wins = sum(1 for net, _ in items if net > 0)
        beat = sum(1 for _, excess in items if excess > 0)
        return {
            "picks": n,
            "winners": wins,
            "win_rate": wins / n if n else None,
            "beat_market": beat,
            "beat_market_rate": beat / n if n else None,
        }

    out: dict[str, Any] = {}
    for group, picks in seen.items():
        sessions = sorted({k[0] for k in picks})
        latest = sessions[-1] if sessions else None
        out[group] = {
            "all": tally(list(picks.values())),
            "latest_session": latest,
            "latest": tally([v for k, v in picks.items() if k[0] == latest]),
            "sessions": len(sessions),
        }
    return out


# ----------------------------------------------------------------------- observation


def observe_movers(frames: Mapping[str, pd.DataFrame], session: date) -> dict[str, Any]:
    """For the session's eventual +5% movers: what did they look like after the first hour,
    and how much of their day was still left from the 10:30 entry?"""

    f = frames["first_hour"]
    day = f[f["date"] == session].dropna(subset=["prev_close", "close_last", "entry_4"])
    if day.empty:
        return {"session": session.isoformat(), "status": "no_intraday_data"}
    move = day["close_last"] / day["prev_close"] - 1
    movers, others = day[move >= daily.MOVE], day[move < daily.MOVE]

    def med(frame: pd.DataFrame, col: str) -> float | None:
        values = frame[col].dropna()
        return float(values.median()) if len(values) else None

    up_early = int((movers["r_open"] >= 0.02).sum())
    return {
        "session": session.isoformat(),
        "status": "observed",
        "liquid_names": int(len(day)),
        "movers": int(len(movers)),
        "movers_up_2pct_by_10_15": up_early,
        "share_of_all_up_2pct_by_10_15_that_became_movers": (
            up_early / int((day["r_open"] >= 0.02).sum())
            if int((day["r_open"] >= 0.02).sum())
            else None
        ),
        "median_r_open_movers": med(movers, "r_open"),
        "median_r_open_others": med(others, "r_open"),
        "median_vol_rel_movers": med(movers, "vol_rel"),
        "median_vol_rel_others": med(others, "vol_rel"),
        "median_rest_of_day_movers_from_10_30": med(movers, "rest_of_day"),
        "median_rest_of_day_all_from_10_30": med(day, "rest_of_day"),
    }


def nightly(market: str, *, root: Path | None = None, update_days: int = 5) -> dict[str, Any]:
    import asyncio

    now = datetime.now(IST)
    root = root or OUTPUT_ROOT / market
    update = asyncio.run(intraday_history.backfill(market, update_days, refresh=True))
    frames = add_features(stored_session_aggregates(market, now - timedelta(days=60), now), market)
    sessions = sorted(frames["first_hour"]["date"].unique())
    latest = sessions[-1] if sessions else None
    costs = costs_for(market)
    scores = forward_scores(root, frames, costs)
    report = {
        "version": VERSION,
        "market": market,
        "generated_at": now.isoformat(),
        "authority": "research_only",
        "history_update": {k: update.get(k) for k in ("wanted", "todo", "stored")},
        "latest_observation": observe_movers(frames, latest) if latest else None,
        "forward": scores,
        "pick_record": pick_record(root, frames, costs),
        "review_items_added": daily.flag_cleared(root, market, scores),
        "active_candidates": [
            f"{c['name']} / {c['target']}" for c in daily.active_candidates(root)
        ],
    }
    obs_path = root / "observations.jsonl"
    seen = obs_path.read_text("utf-8") if obs_path.exists() else ""
    obs = report["latest_observation"]
    if obs and f'"session": "{obs["session"]}"' not in seen:
        obs_path.parent.mkdir(parents=True, exist_ok=True)
        with obs_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(obs, sort_keys=True) + "\n")
    last_search = root / "search_latest.json"
    if last_search.exists():
        found = json.loads(last_search.read_text("utf-8"))
        report["last_search"] = {
            "searched_at": found.get("searched_at"),
            "tested": found.get("tested"),
            "survivors": [f"{s['name']} / {s['target']}" for s in found.get("survivors", [])],
        }
    daily._write(root / "latest.json", report)
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradedesk_lab.intraday_mover_study")
    parser.add_argument("command", choices=["search", "nightly", "observe-history"])
    parser.add_argument("--market", choices=["nse", "bse", "crypto"], default="nse")
    args = parser.parse_args(argv)
    if args.command == "search":
        result = run_search(args.market)
        brief = {
            "tested": result["tested"],
            "survivors": [f"{s['name']} / {s['target']}" for s in result["survivors"]],
            "activated": result["activated"],
            "checkpoints": {
                k: {x: v.get(x) for x in ("sessions", "passed_train", "validation_bar_t")}
                for k, v in result["checkpoints"].items()
            },
        }
        print(json.dumps(brief, indent=2, default=str))
    elif args.command == "nightly":
        print(json.dumps(nightly(args.market), indent=2, default=str)[:4000])
    else:
        now = datetime.now(IST)
        start = now - timedelta(days=400)
        frames = add_features(stored_session_aggregates(args.market, start, now), args.market)
        sessions = sorted(frames["first_hour"]["date"].unique())
        rows = [observe_movers(frames, s) for s in sessions[-250:]]
        root = OUTPUT_ROOT / args.market
        root.mkdir(parents=True, exist_ok=True)
        (root / "observations.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
        )
        print(json.dumps({"sessions": len(rows)}))


if __name__ == "__main__":
    main()
