"""Learn from each day's big movers - without fooling itself.

Every day some stocks and coins rise 5%+; seeing them afterwards is easy, catching them is
not. This loop does three things, nightly and unattended:

1. OBSERVE (all markets): every liquid stock/coin that closed >= +5%, split into how much of
   the move was already in the opening gap (uncatchable once you see it) versus open->close
   (catchable by someone who bought at the open), and - for NSE - which exchange filings
   (tradedesk/filings.py) landed overnight or during the session. This is the honest daily
   answer to "could we have caught it?".
2. SEARCH (weekly, NSE/BSE): thousands of "what was true at the previous close" conditions
   (cross-sectional deciles/quintiles of the movers panel's causal features, pairs of them,
   results due) are tested for a TRADEABLE edge - next open->close (intraday) and next open
   ->5th close (delivery) - after real costs, measured against every other liquid stock on
   the same day (a matched stock-selection null, day-clustered t). Chronological 70/30
   split; validation must clear a Bonferroni-corrected bar over every train survivor.
3. FORWARD (nightly, NSE/BSE): survivors are activated in a hash-chained registry and from
   then on register their picks after each close, BEFORE the next open (a late registration
   is refused, never back-filled). Only these forward picks can ever count; a candidate
   that clears the forward bar becomes a review_queue item - never an alert or an order.

Hindsight is used to generate ideas only; trust comes from step 3 alone. Most nights the
honest result will be "nothing new survived" - that is the system working, not failing.
Crypto runs step 1 only for now (its daily bar opens 05:30 IST, which needs an intraday
entry definition before steps 2-3 can be fair).
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.models import TradeType
from tradedesk.prediction_ledger import canonical_sha256
from tradedesk_lab.artifacts import OUTPUT

VERSION = "mover-study-v1"
OUTPUT_ROOT = OUTPUT / "mover_study"
MOVE = 0.05
CATCHABLE = 0.02  # open->close a buyer at the open could still have made
MARKETS = ("nse", "bse", "crypto")
SEARCH_MARKETS = ("nse", "bse")
FEATURES = (
    "below50", "pos6m", "d5", "ret1", "ret20", "gap", "close_loc",
    "atr_pct", "dist_hi20", "rng_rel", "vol_rel", "hist_res_move",
)  # fmt: skip
TARGETS: dict[str, tuple[int, TradeType]] = {
    "open_to_close_1d": (1, TradeType.INTRADAY),
    "open_to_close_5d": (5, TradeType.DELIVERY),
}
TRAIN_FRACTION = 0.7
MIN_TRAIN_PICKS = 300
MIN_TRAIN_DAYS = 100
MIN_TRAIN_T = 3.0
MIN_VALIDATION_DAYS = 40
FAMILY_ALPHA = 0.05
FORWARD_MIN_DAYS = 40
FORWARD_MIN_T = 2.0
NOMINAL_NOTIONAL = 50_000.0
EQUITY_OPEN = time(9, 15)

# ---------------------------------------------------------------------------- data


def load_panel(market: str, db_path: Path | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
    """Causal features per (stock, session) from the movers screen's own panel builder, plus
    the raw OHLC needed for tradeable targets and gap decomposition."""

    from tradedesk.potential_movers import _read_daily, build_panel, read_results_dates

    frame, names = _read_daily(market, db_path)
    results = read_results_dates(market, db_path) if market != "crypto" else {}
    panel = build_panel(frame, market, results)
    if panel.empty:
        return panel, names
    raw = frame.drop_duplicates(["scrip_code", "ts"]).copy()
    raw["date"] = (
        pd.to_datetime(raw["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    )
    raw = raw.sort_values(["scrip_code", "date"]).rename(columns={"scrip_code": "code"})
    g = raw.groupby("code", sort=False)
    raw["open_today"] = raw["open"].astype(float)
    raw["high_today"] = raw["high"].astype(float)
    raw["prev_close"] = g["close"].shift(1).astype(float)
    next_open = g["open"].shift(-1).astype(float)
    for name, (horizon, _) in TARGETS.items():
        raw[name] = g["close"].shift(-horizon).astype(float) / next_open - 1
    keep = ["code", "date", "open_today", "high_today", "prev_close", *TARGETS]
    panel = panel.merge(raw[keep], on=["code", "date"], how="left")
    return panel, names


def cost_fraction(market: str, trade_type: TradeType) -> float:
    """Round-trip cost plus slippage on a nominal Rs 50k position, as a fraction."""

    from tradedesk.config import load_config
    from tradedesk.markets.market import bse_market, crypto_market, nse_market
    from tradedesk_lab.artifacts import ROOT

    settings = load_config(ROOT)
    mkt = {"bse": bse_market, "crypto": crypto_market}.get(market, nse_market)(settings)
    price = 500.0
    qty = NOMINAL_NOTIONAL / price
    cost = mkt.costs.round_trip_cost(
        trade_type=trade_type, qty=qty, entry_price=Decimal("500"), exit_price=Decimal("500")
    )
    return float(cost.total) / NOMINAL_NOTIONAL + 2 * float(mkt.costs.slippage_pct)


# ------------------------------------------------------------------------- observe


def observe(
    panel: pd.DataFrame,
    names: Mapping[str, str],
    session: date,
    *,
    market: str,
    filings: Sequence[Any] = (),
) -> dict[str, Any]:
    """What moved on `session`, how much of it was catchable, and what was filed."""

    day = panel[(panel["date"] == session) & panel["prev_close"].notna()].copy()
    if day.empty:
        return {"session": session.isoformat(), "market": market, "status": "no_data"}
    day["move"] = day["close"] / day["prev_close"] - 1
    day["gap_part"] = day["open_today"] / day["prev_close"] - 1
    day["open_to_close"] = day["close"] / day["open_today"] - 1
    day["open_to_high"] = day["high_today"] / day["open_today"] - 1
    movers = day[day["move"] >= MOVE].sort_values("move", ascending=False)
    by_symbol: dict[str, list[Any]] = {}
    for filing in filings:
        by_symbol.setdefault(str(filing.symbol).upper(), []).append(filing)
    open_at = datetime.combine(session, EQUITY_OPEN, tzinfo=IST)
    rows = []
    for _, r in movers.iterrows():
        symbol = names.get(str(r["code"]), str(r["code"]))
        own = [f for f in by_symbol.get(symbol.upper(), []) if f.category != "routine"]
        rows.append(
            {
                "symbol": symbol,
                "code": str(r["code"]),
                "move": round(float(r["move"]), 4),
                "gap_part": round(float(r["gap_part"]), 4),
                "open_to_close": round(float(r["open_to_close"]), 4),
                "open_to_high": round(float(r["open_to_high"]), 4),
                "results_due": bool(r.get("res_today", 0) > 0),
                "filings_overnight": sorted({f.category for f in own if f.when < open_at}),
                "filings_intraday": [
                    {"at": f.filed_at[11:16], "category": f.category}
                    for f in own
                    if f.when >= open_at
                ],
            }
        )
    n = len(rows)
    gap_share = [
        max(0.0, min(1.0, row["gap_part"] / row["move"])) for row in rows if row["move"] > 0
    ]
    summary = {
        "liquid_names": int(len(day)),
        "movers": n,
        "mover_rate": n / len(day) if len(day) else None,
        "fallers": int((day["move"] <= -MOVE).sum()),
        "median_share_of_move_in_gap": float(np.median(gap_share)) if gap_share else None,
        "catchable_after_open": sum(row["open_to_close"] >= CATCHABLE for row in rows),
        "mean_open_to_close": float(np.mean([row["open_to_close"] for row in rows]))
        if rows
        else None,  # noqa: E501
        "with_material_filing": sum(
            bool(row["filings_overnight"] or row["filings_intraday"]) for row in rows
        ),
        "with_results": sum(
            row["results_due"]
            or "results" in row["filings_overnight"]
            or any(x["category"] == "results" for x in row["filings_intraday"])
            for row in rows
        ),
        "filings_intraday": sum(bool(row["filings_intraday"]) for row in rows),
    }
    return {
        "version": VERSION,
        "market": market,
        "session": session.isoformat(),
        "status": "observed",
        "summary": summary,
        "movers": rows[:60],
    }


# -------------------------------------------------------------------------- search


@dataclass(frozen=True)
class Clause:
    feature: str  # a FEATURES name, or "res_next"
    side: str  # "top" | "bottom" | "flag"
    quantile: float  # top: pct >= 1-q ; bottom: pct <= q

    def label(self) -> str:
        if self.side == "flag":
            return "results_due_next"
        return f"{self.feature}_{self.side}{int(round(self.quantile * 100))}"


def conditions(
    features: Sequence[str] = FEATURES, *, results_flag: bool = True
) -> list[tuple[Clause, ...]]:
    """The fixed search grammar (313 conditions for the daily study); changing it means a
    new VERSION. Deciles of each feature, pairs of quintiles, and (equity daily) results
    due next session alone and with each quintile."""

    singles = [(Clause(f, side, 0.10),) for f in features for side in ("top", "bottom")]
    quint = [Clause(f, side, 0.20) for f in features for side in ("top", "bottom")]
    pairs = [(a, b) for a, b in combinations(quint, 2) if a.feature != b.feature]
    if not results_flag:
        return [*singles, *pairs]
    results = Clause("res_next", "flag", 0.0)
    with_results = [(results,), *((results, q) for q in quint)]
    return [*singles, *pairs, *with_results]


def condition_name(condition: Sequence[Clause]) -> str:
    return "&".join(clause.label() for clause in condition)


def add_ranks(frame: pd.DataFrame, features: Sequence[str] = FEATURES) -> pd.DataFrame:
    out = frame.copy()
    for feature in features:
        out[f"{feature}_pct"] = out.groupby("date")[feature].rank(pct=True)
    return out


def mask_for(frame: pd.DataFrame, condition: Sequence[Clause]) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for clause in condition:
        if clause.side == "flag":
            mask &= frame.get("res_next", 0) > 0
        elif clause.side == "top":
            mask &= frame[f"{clause.feature}_pct"] >= 1 - clause.quantile
        else:
            mask &= frame[f"{clause.feature}_pct"] <= clause.quantile
    return mask


def _day_stats(picks: pd.DataFrame, target: str, cost: float) -> dict[str, float]:
    days = picks.groupby("date").agg(
        excess=(f"{target}_excess", "mean"), gross=(target, "mean"), n=(target, "size")
    )
    n_days = len(days)
    mean_excess = float(days["excess"].mean()) if n_days else 0.0
    sd = float(days["excess"].std(ddof=1)) if n_days > 1 else float("nan")
    t = mean_excess / (sd / math.sqrt(n_days)) if n_days > 1 and sd > 0 else 0.0
    gross = float(days["gross"].mean()) if n_days else 0.0
    return {
        "days": n_days,
        "picks": int(days["n"].sum()),
        "mean_gross": gross,
        "mean_net": gross - cost,
        "mean_excess": mean_excess,
        "t_excess": t,
    }


def _norm_ppf(p: float) -> float:
    from statistics import NormalDist

    return NormalDist().inv_cdf(p)


def train_phase(
    panel: pd.DataFrame,
    costs: Mapping[str, float],
    *,
    grammar: Sequence[tuple[Clause, ...]] | None = None,
    features: Sequence[str] = FEATURES,
    targets: Sequence[str] = tuple(TARGETS),
) -> dict[str, Any]:
    """Rank, split chronologically (first 70% of sessions train), and keep every condition x
    target that clears the training bar. `date` is the grouping key for the same-day stock
    null and the day-clustered t."""

    frame = panel.dropna(subset=[*features, *targets]).copy()
    frame = add_ranks(frame, features)
    for target in targets:
        frame[f"{target}_excess"] = frame[target] - frame.groupby("date")[target].transform("mean")
    dates = sorted(frame["date"].unique())
    if len(dates) < MIN_TRAIN_DAYS + MIN_VALIDATION_DAYS:
        return {"status": "insufficient_history", "sessions": len(dates), "passed": []}
    split = dates[int(len(dates) * TRAIN_FRACTION)]
    train, validation = frame[frame["date"] < split], frame[frame["date"] >= split]
    grammar = list(grammar or conditions(features))
    passed: list[dict[str, Any]] = []
    tested = 0
    for condition in grammar:
        train_mask = mask_for(train, condition)
        for target in targets:
            tested += 1
            stats = _day_stats(train[train_mask], target, costs[target])
            if (
                stats["picks"] >= MIN_TRAIN_PICKS
                and stats["days"] >= MIN_TRAIN_DAYS
                and stats["t_excess"] >= MIN_TRAIN_T
                and stats["mean_net"] > 0
            ):
                passed.append({"condition": condition, "target": target, "train": stats})
    return {
        "status": "trained",
        "sessions": len(dates),
        "train": [str(dates[0]), str(split)],
        "validation_from": str(split),
        "validation_to": str(dates[-1]),
        "tested": tested,
        "passed": passed,
        "validation": validation,
    }


def family_bar(n_passed: int) -> float | None:
    """One-sided Bonferroni bar over EVERY train survivor in the family being validated."""

    return _norm_ppf(1 - FAMILY_ALPHA / n_passed) if n_passed else None


def validate_phase(
    trained: dict[str, Any], costs: Mapping[str, float], bar: float | None
) -> dict[str, Any]:
    if trained.get("status") != "trained":
        return {k: v for k, v in trained.items() if k not in ("passed", "validation")}
    validation = trained["validation"]
    passed = trained["passed"]
    survivors = []
    for item in passed:
        val_mask = mask_for(validation, item["condition"])
        stats = _day_stats(validation[val_mask], item["target"], costs[item["target"]])
        item["validation"] = stats
        item["survived"] = bool(
            stats["days"] >= MIN_VALIDATION_DAYS
            and stats["t_excess"] >= (bar or 0)
            and stats["mean_net"] > 0
        )
        if item["survived"]:
            survivors.append(item)
    return {
        "status": "searched",
        "sessions": trained["sessions"],
        "train": trained["train"],
        "validation_from": trained["validation_from"],
        "validation_to": trained["validation_to"],
        "tested": trained["tested"],
        "passed_train": len(passed),
        "validation_bar_t": bar,
        "survivors": [_public(item) for item in survivors],
        "near_misses": [
            _public(item)
            for item in sorted(
                (i for i in passed if not i["survived"]),
                key=lambda i: -i["validation"]["t_excess"],
            )[:10]
        ],
        "costs": dict(costs),
    }


def search(
    panel: pd.DataFrame,
    costs: Mapping[str, float],
    *,
    grammar: Sequence[tuple[Clause, ...]] | None = None,
    features: Sequence[str] = FEATURES,
    targets: Sequence[str] = tuple(TARGETS),
) -> dict[str, Any]:
    """Train on the first 70% of sessions, validate the survivors on the rest against a
    Bonferroni bar over this search's own train survivors."""

    trained = train_phase(panel, costs, grammar=grammar, features=features, targets=targets)
    return validate_phase(trained, costs, family_bar(len(trained.get("passed", []))))


def _public(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": condition_name(item["condition"]),
        "clauses": [vars(c) for c in item["condition"]],
        "target": item["target"],
        "train": item["train"],
        "validation": item.get("validation"),
    }


# ------------------------------------------------------------------- hash-chained log


def read_chain(path: Path) -> tuple[list[dict[str, Any]], bool]:
    if not path.exists():
        return [], True
    rows, intact, previous = [], True, "GENESIS"
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


def append_chain(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    rows, _ = read_chain(path)
    body = {**record, "previous_sha256": rows[-1]["sha256"] if rows else "GENESIS"}
    body["sha256"] = canonical_sha256(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(body, sort_keys=True, default=str) + "\n")
    return body


# ------------------------------------------------------------------------- forward


def next_open(session: date) -> datetime:
    day = session + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, EQUITY_OPEN, tzinfo=IST)


def active_candidates(root: Path) -> list[dict[str, Any]]:
    rows, _ = read_chain(root / "candidates.jsonl")
    return [r for r in rows if r.get("event") == "candidate_activated"]


def activate(root: Path, result: Mapping[str, Any], *, now: datetime) -> list[str]:
    """Put new survivors into forward tracking; an active candidate is never re-added."""

    known = {(c["name"], c["target"]) for c in active_candidates(root)}
    added = []
    for survivor in result.get("survivors", []):
        if (survivor["name"], survivor["target"]) in known:
            continue
        append_chain(
            root / "candidates.jsonl",
            {
                "event": "candidate_activated",
                "version": VERSION,
                "activated_at": now.isoformat(),
                **survivor,
            },
        )
        added.append(survivor["name"])
    return added


def register_picks(
    root: Path,
    panel: pd.DataFrame,
    names: Mapping[str, str],
    session: date,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Each active candidate's picks for the session after `session`, decided at its close.
    Refused once that next session has opened: a late pick is never back-filled."""

    deadline = next_open(session)
    if now >= deadline:
        return {"status": "too_late", "deadline": deadline.isoformat()}
    path = root / "forward_picks.jsonl"
    rows, _ = read_chain(path)
    done = {(r["candidate"], r["target"], r["decision_session"]) for r in rows}
    today = add_ranks(panel[panel["date"] == session].dropna(subset=list(FEATURES)))
    registered = 0
    for candidate in active_candidates(root):
        key = (candidate["name"], candidate["target"], session.isoformat())
        if key in done or str(candidate["activated_at"])[:10] > session.isoformat():
            continue
        condition = tuple(Clause(**c) for c in candidate["clauses"])
        codes = sorted(str(c) for c in today.loc[mask_for(today, condition), "code"])
        append_chain(
            path,
            {
                "event": "picks_registered",
                "candidate": candidate["name"],
                "target": candidate["target"],
                "decision_session": session.isoformat(),
                "registered_at": now.isoformat(),
                "deadline": deadline.isoformat(),
                "codes": codes,
                "symbols": [names.get(c, c) for c in codes],
            },
        )
        registered += 1
    return {"status": "registered", "candidates": registered, "deadline": deadline.isoformat()}


def forward_scores(
    root: Path, panel: pd.DataFrame, costs: Mapping[str, float]
) -> list[dict[str, Any]]:
    """Score only picks registered before their deadline, against every liquid stock on the
    same decision session."""

    rows, intact = read_chain(root / "forward_picks.jsonl")
    if not rows:
        return []
    frame = panel.dropna(subset=list(TARGETS)).copy()
    by_date = {d: g.set_index("code") for d, g in frame.groupby("date")}
    stats: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in rows:
        if row.get("event") != "picks_registered" or datetime.fromisoformat(
            row["registered_at"]
        ) >= datetime.fromisoformat(row["deadline"]):
            continue
        day = by_date.get(date.fromisoformat(row["decision_session"]))
        target = row["target"]
        if day is None or not row["codes"]:
            continue
        picked = day.reindex(row["codes"])[target].dropna()
        if picked.empty:
            continue
        stats.setdefault((row["candidate"], target), []).append(
            (float(picked.mean()), float(picked.mean() - day[target].mean()))
        )
    out = []
    for (name, target), values in stats.items():
        gross = np.array([v[0] for v in values])
        excess = np.array([v[1] for v in values])
        n = len(values)
        t = (
            float(excess.mean() / (excess.std(ddof=1) / math.sqrt(n)))
            if n > 1 and excess.std(ddof=1) > 0
            else 0.0
        )  # noqa: E501
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
                    n >= FORWARD_MIN_DAYS and net > 0 and t >= FORWARD_MIN_T
                ),
                "chain_intact": intact,
            }
        )
    return out


def flag_cleared(root: Path, market: str, scores: Iterable[Mapping[str, Any]]) -> list[str]:
    """A candidate that clears the forward bar becomes ONE review item - never a call."""

    from tradedesk.review_queue import add_item

    flagged_path = root / "review_flagged.json"
    flagged = set(json.loads(flagged_path.read_text("utf-8"))) if flagged_path.exists() else set()
    new = []
    for score in scores:
        key = f"{score['candidate']}|{score['target']}"
        if not score["cleared_forward_bar"] or key in flagged:
            continue
        add_item(
            market,
            f"Mover study candidate cleared its forward bar: {score['candidate']}",
            json.dumps(score, sort_keys=True),
            "Review only. A forward-cleared mover-study condition may be worth a full "
            "gauntlet + eligibility review; it is not an alert, a signal or an order.",
        )
        flagged.add(key)
        new.append(key)
    flagged_path.parent.mkdir(parents=True, exist_ok=True)
    flagged_path.write_text(json.dumps(sorted(flagged)), encoding="utf-8")
    return new


# ---------------------------------------------------------------------- orchestration


def latest_complete_session(panel: pd.DataFrame, market: str, now: datetime) -> date | None:
    if panel.empty:
        return None
    last = max(panel["date"])
    if market == "crypto":
        # the UTC daily bar for `last` closes at 05:30 IST the next day
        if now < datetime.combine(last + timedelta(days=1), time(5, 30), tzinfo=IST):
            earlier = sorted({d for d in panel["date"] if d < last})
            return earlier[-1] if earlier else None
        return last
    if last == now.date() and now.time() < time(15, 45):
        earlier = sorted({d for d in panel["date"] if d < last})
        return earlier[-1] if earlier else None
    return last


def nightly(
    market: str, *, root: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    from tradedesk import filings as filings_module

    now = (now or datetime.now(IST)).astimezone(IST)
    root = root or OUTPUT_ROOT / market
    panel, names = load_panel(market)
    session = latest_complete_session(panel, market, now)
    if session is None:
        return {"market": market, "status": "no_data"}
    day_filings: list[Any] = []
    if market in ("nse", "bse"):
        start = filings_module.previous_close(datetime.combine(session, time(12, 0), tzinfo=IST))
        day_filings = filings_module.load(
            start, datetime.combine(session, time(15, 30), tzinfo=IST)
        )
    observation = observe(panel, names, session, market=market, filings=day_filings)
    obs_path = root / "observations.jsonl"
    seen = obs_path.read_text("utf-8") if obs_path.exists() else ""
    if f'"session": "{session.isoformat()}"' not in seen:
        obs_path.parent.mkdir(parents=True, exist_ok=True)
        with obs_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(observation, sort_keys=True) + "\n")
    report: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "generated_at": now.isoformat(),
        "authority": "research_only",
        "latest_observation": observation,
        "recent": _recent_summaries(obs_path),
    }
    if market in SEARCH_MARKETS:
        costs = {t: cost_fraction(market, tt) for t, (_, tt) in TARGETS.items()}
        report["registration"] = register_picks(root, panel, names, session, now=now)
        scores = forward_scores(root, panel, costs)
        report["forward"] = scores
        report["review_items_added"] = flag_cleared(root, market, scores)
        report["active_candidates"] = [
            c["name"] + " / " + c["target"] for c in active_candidates(root)
        ]
        last_search = root / "search_latest.json"
        if last_search.exists():
            report["last_search"] = _search_brief(json.loads(last_search.read_text("utf-8")))
    _write(root / "latest.json", report)
    return report


def run_search(
    market: str, *, root: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    now = (now or datetime.now(IST)).astimezone(IST)
    root = root or OUTPUT_ROOT / market
    panel, _ = load_panel(market)
    costs = {t: cost_fraction(market, tt) for t, (_, tt) in TARGETS.items()}
    result = search(panel, costs)
    result["searched_at"] = now.isoformat()
    result["activated"] = (
        activate(root, result, now=now) if result.get("status") == "searched" else []
    )
    _write(root / "search_latest.json", result)
    return result


def _search_brief(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: result.get(k)
        for k in ("searched_at", "tested", "passed_train", "validation_bar_t", "activated")
    } | {"survivors": [s["name"] + " / " + s["target"] for s in result.get("survivors", [])]}


def _recent_summaries(path: Path, n: int = 20) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    return [{"session": r["session"], **(r.get("summary") or {})} for r in rows[-n:]]


def _write(path: Path, value: Mapping[str, Any]) -> None:
    from tradedesk_lab.artifacts import write_json

    write_json(path, json.loads(json.dumps(value, default=str)))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradedesk_lab.mover_study")
    parser.add_argument("command", choices=["nightly", "search", "backfill-observations"])
    parser.add_argument("--market", choices=MARKETS, action="append")
    parser.add_argument("--sessions", type=int, default=60)
    args = parser.parse_args(argv)
    markets = args.market or (list(SEARCH_MARKETS) if args.command == "search" else list(MARKETS))
    out: dict[str, Any] = {}
    for market in markets:
        if args.command == "nightly":
            out[market] = _brief(nightly(market))
        elif args.command == "search":
            if market not in SEARCH_MARKETS:
                continue
            result = run_search(market)
            out[market] = _search_brief(result)
        else:
            out[market] = backfill_observations(market, args.sessions)
    print(json.dumps(out, indent=2, default=str))


def backfill_observations(
    market: str, sessions: int, *, root: Path | None = None
) -> dict[str, Any]:
    """Observations for past sessions (descriptive only - never forward evidence)."""

    from tradedesk import filings as filings_module

    root = root or OUTPUT_ROOT / market
    panel, names = load_panel(market)
    days = sorted(set(panel["date"]))[-sessions:]
    path = root / "observations.jsonl"
    seen = path.read_text("utf-8") if path.exists() else ""
    rows = []
    for session in days:
        if f'"session": "{session.isoformat()}"' in seen:
            continue
        day_filings: list[Any] = []
        if market in ("nse", "bse"):
            start = filings_module.previous_close(
                datetime.combine(session, time(12, 0), tzinfo=IST)
            )
            day_filings = filings_module.load(
                start, datetime.combine(session, time(15, 30), tzinfo=IST)
            )
        rows.append(observe(panel, names, session, market=market, filings=day_filings))
    existing = [json.loads(line) for line in seen.splitlines() if line.strip()]
    merged = sorted(existing + rows, key=lambda r: r["session"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in merged), encoding="utf-8")
    return {"added": len(rows), "total": len(merged)}


def _brief(report: Mapping[str, Any]) -> dict[str, Any]:
    obs = report.get("latest_observation") or {}
    return {
        "session": obs.get("session"),
        "summary": obs.get("summary"),
        "registration": report.get("registration"),
        "active_candidates": report.get("active_candidates"),
    }


if __name__ == "__main__":
    main()
