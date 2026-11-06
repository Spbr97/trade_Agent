"""Positioning: do FII and retail positions in index futures say where the Nifty goes next?

First principles (owner, 2026-11-05: aim for a breakthrough). Price rules on the Nifty failed
(edge_hunt.py), but they only watched the tape. NSE publishes each evening who actually holds
what: FII, retail clients, DII and proprietary traders, contracts long and short. Who is on the
other side? Large funds position and hedge in size, retail clients often sit opposite them, and
the question is which side keeps being right. The risk: this table is widely watched, so it may be
priced by the next open, and much of an FII short book is a hedge against stocks they own, not a
view.

Data: fao_participant_oi_DDMMYYYY.csv from NSE's archive, each session from 2018, saved by `fetch`
to data/m14_m18/positioning/participant_oi.csv. A file whose title date is not the session asked
for is refused. `coverage` shows sessions per year and the long share's range; check it before a
result.

Long share = index-futures long / (long + short), for a participant. Its rank is taken against its
own previous 250 sessions and that day, so nothing looks ahead. The file comes out after the close
of day D, so a signal from D is entered at D+1's open.

Rules (a long and a short side, one position at a time, none until the last one ends):
  fii_follow   long when FII's long share ranks in its top 20%, short in its bottom 20%;
  fii_fade     the opposite;
  client_fade  short when retail clients' long share ranks in its top 20%, long in its bottom 20%;
  fii_shift    long when FII's long share rose 5 points or more over 5 sessions, short if it fell.
Holds: 1 session (open to that close, a day trade), 5 and 20 sessions. Traded on Nifty's index
open and close as the stand-in for its future, at edge_hunt's costs: 0.04% each way and 5.2% a
year of carry, paid by a long and earned by a short, over multi-day holds. Rs 10,000 is not
possible in futures, so the amounts study does not apply: one lot is about Rs 16 lakh of exposure.

The Nifty tends to rise, so a long side can look clever only by being long. Excess = side x (the
trade's move less the average move of every session's same hold) less costs.

PASS BAR (written 2026-11-07, before any positioning data was downloaded). A rule and hold passes
only if: 1. at least 60 trades; 2. mean net > 0 after costs; 3. mean excess > 0 with t >= 3.0, one
trade as one unit, since positions do not overlap; 4. positive mean excess in both halves (split
at the median entry date); 5. positive mean excess on BOTH the long and the short side; 6.
positive excess in at least 60% of calendar years with 5 or more trades. 4 rules x 3 holds = 12
variants; t >= 3 is stricter than Bonferroni's bar for twelve. A pass becomes a proposal for
FORWARD paper trades.

Drift filter, judged apart. Do drift calls do better when positioning is friendly? Live-rule
calls, split by the rank on the reaction close: the bottom third against the rest, for FII (bottom
= FII least long) and clients (top third = retail most long, the rank reversed). Passes only if
the kept group's mean excess beats the dropped group's with t >= 2.5 over entry weeks, in both
halves, and the kept group makes more rupees a call. 2 variants. A pass is a proposal, not a
change to the live rule.

RESULT (2026-11-07, 2,188 of 2,193 sessions 2018-2026, no stale tables, trades from 2019): ALL 12
FAIL, AND BOTH DRIFT FILTERS FAIL. Day trades lose to costs either way: fii_follow_1 -0.06% a trade,
fii_fade_1 -0.10%, client_fade_1 -0.04%, fii_shift_1 -0.05%, positive in at most 25% of years. Best:
client_fade_20 (75 trades, +0.48% net, +0.63% vs drift, t 1.05). fii_follow_5 +0.11%, t 0.80, first
half only. Drift calls with friendly FII positioning make far more rupees (+2.20% against +0.08%)
but no more over the market (t -0.34; clients t -0.17): friendly positioning only marks a rising
market. Next-day and multi-week Nifty direction is not in this table, or is priced by the next open.

    python -m tradedesk_lab.positioning fetch
    python -m tradedesk_lab.positioning
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import time as time_module
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import drift_runner as dr
from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = ROOT / "data" / "m14_m18" / "positioning"
TABLE = STORE / "participant_oi.csv"
OUT_DIR = OUTPUT / "positioning"
URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{:%d%m%Y}.csv"
FIRST = date(2018, 1, 1)
PARTIES = ("Client", "DII", "FII", "Pro")
RANK_WINDOW = 250
TOP, BOTTOM = 0.80, 0.20
SHIFT_POINTS, SHIFT_SESSIONS = 0.05, 5
HOLDS = (1, 5, 20)
RULES = ("fii_follow", "fii_fade", "client_fade", "fii_shift")
PASS = {"min_trades": 60, "min_t": 3.0, "min_year_share": 0.6, "drift_min_t": 2.5}
MIN_YEAR_TRADES = 5
_TITLE = re.compile(r"as on\s+([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})")
FIELDS = ("day", "party", "fut_idx_long", "fut_idx_short", "opt_call_long", "opt_put_long",
          "opt_call_short", "opt_put_short")  # fmt: skip


# --------------------------------------------------------------------------- data


def parse_file(text: str, wanted: date) -> list[dict[str, Any]] | None:
    """The participants' rows, or None if the file is not that session's or cannot be read."""

    lines = text.splitlines()
    at = next((i for i, line in enumerate(lines[:3]) if line.startswith("Client Type")), None)
    if at is None:
        return None
    match = _TITLE.search(" ".join(lines[:at]))
    if match:  # some 2018 files have no title; stale_days checks those afterwards
        month, day, year = match.groups()
        stated = None
        for form in ("%b", "%B"):
            try:
                stated = datetime.strptime(f"{month} {day} {year}", f"{form} %d %Y").date()
                break
            except ValueError:
                continue
        if stated != wanted:
            return None
    rows = []
    for raw in csv.DictReader(io.StringIO("\n".join(lines[at:]))):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        party = row.get("Client Type", "")
        if party not in PARTIES:
            continue
        try:
            rows.append({
                "day": wanted.isoformat(), "party": party,
                "fut_idx_long": float(row["Future Index Long"]),
                "fut_idx_short": float(row["Future Index Short"]),
                "opt_call_long": float(row["Option Index Call Long"]),
                "opt_put_long": float(row["Option Index Put Long"]),
                "opt_call_short": float(row["Option Index Call Short"]),
                "opt_put_short": float(row["Option Index Put Short"]),
            })  # fmt: skip
        except (KeyError, ValueError):
            return None
    return rows if {r["party"] for r in rows} == set(PARTIES) else None


def fetch(
    sessions: Iterable[date], *, path: Path = TABLE, pause_seconds: float = 0.4
) -> dict[str, int]:
    """Save each session's rows once. Resumable. A 404 is a day with no file, and is counted."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    path.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if path.exists():
        done = set(pd.read_csv(path, usecols=["day"])["day"].astype(str))
    new_file = not path.exists()
    counts = {"saved": 0, "missing": 0, "refused": 0}
    errors = 0
    with (
        httpx.Client(headers=_NSE_HEADERS, timeout=60, follow_redirects=True) as client,
        path.open("a", encoding="utf-8", newline="") as out,
    ):
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for day in sessions:
            if day.isoformat() in done or day < FIRST:
                continue
            try:
                response = client.get(URL.format(day))
            except httpx.HTTPError as exc:
                errors += 1
                if errors >= 25:
                    raise RuntimeError(f"{errors} errors in a row, last: {exc}") from exc
                time_module.sleep(min(60.0, pause_seconds * 2**errors))
                continue
            errors = 0
            if response.status_code == 404:
                counts["missing"] += 1
            elif response.status_code != 200:
                response.raise_for_status()
            else:
                rows = parse_file(response.text, day)
                if rows is None:
                    counts["refused"] += 1
                else:
                    writer.writerows(rows)
                    out.flush()
                    counts["saved"] += 1
            time_module.sleep(pause_seconds)
    return counts


def read_table(path: Path = TABLE) -> pd.DataFrame:
    """day x party's index-futures long share."""

    frame = pd.read_csv(path)
    frame["day"] = pd.to_datetime(frame["day"]).dt.date
    frame["share"] = frame["fut_idx_long"] / (frame["fut_idx_long"] + frame["fut_idx_short"])
    return frame.pivot_table(index="day", columns="party", values="share", aggfunc="last")


def stale_days(path: Path = TABLE) -> list[str]:
    """Sessions whose FII row repeats the session before exactly: a stale file, not a new table."""

    frame = pd.read_csv(path)
    fii = frame[frame["party"] == "FII"].sort_values("day")
    columns = list(FIELDS[2:])
    same = (fii[columns] == fii[columns].shift()).all(axis=1)
    return list(fii.loc[same, "day"].astype(str))


def coverage(shares: pd.DataFrame, sessions: Sequence[date]) -> dict[str, dict[str, float]]:
    """Sessions held against sessions traded, and the range of FII's share, by year."""

    out = {}
    held = pd.Series(shares.index).map(lambda d: d.year).value_counts()
    all_days = pd.Series([d for d in sessions if d >= FIRST]).map(lambda d: d.year).value_counts()
    for year in sorted(all_days.index):
        part = shares[[d.year == year for d in shares.index]]["FII"] if year in held else None
        out[str(year)] = {
            "held": int(held.get(year, 0)), "sessions": int(all_days[year]),
            "fii_min": float(part.min()) if part is not None else math.nan,
            "fii_max": float(part.max()) if part is not None else math.nan,
        }  # fmt: skip
    return out


# --------------------------------------------------------------------------- signals


def trailing_rank(series: pd.Series, window: int = RANK_WINDOW) -> pd.Series:
    """Share of the last `window` values, today's included, at or below today's. NaN until full."""

    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    for i in range(window - 1, len(values)):
        past = values[i - window + 1 : i + 1]
        if np.isfinite(past).all():
            out[i] = float((past <= values[i]).mean())
    return pd.Series(out, index=series.index)


def rule_signal(rule: str, shares: pd.DataFrame) -> pd.Series:
    """+1 long, -1 short, 0 nothing, per session, from that session's published table."""

    if rule == "fii_shift":
        change = shares["FII"] - shares["FII"].shift(SHIFT_SESSIONS)
        return (change >= SHIFT_POINTS).astype(int) - (change <= -SHIFT_POINTS).astype(int)
    party = "Client" if rule == "client_fade" else "FII"
    rank = trailing_rank(shares[party])
    high, low = (rank >= TOP).astype(int), (rank <= BOTTOM).astype(int)
    return high - low if rule == "fii_follow" else low - high


def baseline(opens: np.ndarray, closes: np.ndarray, hold: int) -> float:
    """The Nifty's average move over this hold, from every session's open."""

    moves = closes[hold - 1 :] / opens[: len(opens) - hold + 1] - 1
    return float(np.nanmean(moves))


def trades(
    signal: pd.Series, nifty: pd.DataFrame, hold: int, *, base: float | None = None
) -> pd.DataFrame:
    """Non-overlapping trades. A signal from row i enters at row i+1's open and exits at the close
    of row i+hold. The next trade cannot start until after that exit."""

    days = list(nifty.index)
    opens, closes = nifty["open"].to_numpy(float), nifty["close"].to_numpy(float)
    base = baseline(opens, closes, hold) if base is None else base
    aligned = signal.reindex(nifty.index).fillna(0).astype(int).to_numpy()
    rows = []
    i, last = 0, len(days) - 1
    while i + hold <= last:
        side = int(aligned[i])
        if side == 0:
            i += 1
            continue
        e, x = i + 1, i + hold
        move = closes[x] / opens[e] - 1
        carry = 0.0 if hold == 1 else eh.CARRY * hold / 252
        cost = 2 * eh.FUT_COST + (carry if side > 0 else -carry)
        if math.isfinite(move):
            rows.append({"day": days[e], "side": side, "net": side * move - cost,
                         "excess": side * (move - base) - cost})  # fmt: skip
        i = x
    return pd.DataFrame(rows, columns=["day", "side", "net", "excess"])


def judge(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "passed": False, "fails": ["no trades"]}
    n = len(frame)
    excess = frame["excess"]
    sd = float(excess.std(ddof=1)) if n > 1 else 0.0
    t = float(excess.mean() / (sd / math.sqrt(n))) if sd > 0 else math.nan
    days = sorted(frame["day"])
    cut = days[n // 2]
    halves = [float(excess[frame["day"] < cut].mean()), float(excess[frame["day"] >= cut].mean())]
    sides = {s: float(excess[frame["side"] == v].mean()) for s, v in (("long", 1), ("short", -1))}
    counts = {s: int((frame["side"] == v).sum()) for s, v in (("long", 1), ("short", -1))}
    years = frame.groupby(frame["day"].map(lambda d: d.year))["excess"].agg(["count", "mean"])
    judged = years[years["count"] >= MIN_YEAR_TRADES]
    share = float((judged["mean"] > 0).mean()) if len(judged) else 0.0
    out = {
        "trades": n, "mean_net": float(frame["net"].mean()),
        "win_rate": float((frame["net"] > 0).mean()),
        "mean_excess": float(excess.mean()), "t": t, "halves": halves, "sides": sides,
        "side_counts": counts, "year_share": share, "years": len(judged),
        "first": str(days[0]), "last": str(days[-1]),
    }  # fmt: skip
    fails = []
    if n < PASS["min_trades"]:
        fails.append(f"only {n} trades")
    if not out["mean_net"] > 0:
        fails.append("loses money after costs")
    if not (out["mean_excess"] > 0 and t >= PASS["min_t"]):
        fails.append(f"excess t {t:.2f} < {PASS['min_t']}")
    if not all(h > 0 for h in halves):
        fails.append("not positive in both halves")
    if not all(v > 0 for v in sides.values()):
        fails.append("not positive on both sides")
    if share < PASS["min_year_share"]:
        fails.append(f"positive in {share:.0%} of years")
    return out | {"passed": not fails, "fails": fails}


def split_gain(kept: pd.DataFrame, dropped: pd.DataFrame) -> tuple[float, float]:
    """(difference of weekly-mean excess, Welch t), one entry week as one unit in each group."""

    def weeks(frame: pd.DataFrame) -> pd.Series:
        return frame.groupby(frame["day"].map(lambda d: d.isocalendar()[:2]))["excess"].mean()

    a, b = weeks(kept), weeks(dropped)
    if len(a) < 2 or len(b) < 2:
        return math.nan, math.nan
    diff = float(a.mean() - b.mean())
    se = math.sqrt(float(a.var(ddof=1)) / len(a) + float(b.var(ddof=1)) / len(b))
    return diff, diff / se if se > 0 else math.nan


def judge_filter(calls: pd.DataFrame, bad: pd.Series) -> dict[str, Any]:
    """`bad`, aligned to `calls`, marks the calls a filter would drop."""

    kept, dropped = calls[~bad], calls[bad]
    diff, t = split_gain(kept, dropped)
    days = sorted(calls["day"])
    cut = days[len(days) // 2]
    early = calls["day"] < cut
    halves = [split_gain(calls[~bad & early], calls[bad & early])[0],
              split_gain(calls[~bad & ~early], calls[bad & ~early])[0]]  # fmt: skip
    out = {
        "kept": len(kept), "dropped": len(dropped), "kept_net": float(kept["net"].mean()),
        "dropped_net": float(dropped["net"].mean()), "gain": diff, "t": t, "halves": halves,
    }  # fmt: skip
    fails = []
    if not (diff > 0 and t >= PASS["drift_min_t"]):
        fails.append(f"gain t {t:.2f} < {PASS['drift_min_t']}")
    if not all(h > 0 for h in halves):
        fails.append("not positive in both halves")
    if not out["kept_net"] > out["dropped_net"]:
        fails.append("kept calls make no more rupees")
    return out | {"passed": not fails, "fails": fails}


# --------------------------------------------------------------------------- study


def _nifty() -> pd.DataFrame:
    import duckdb

    con = duckdb.connect(str(nl.DB), read_only=True)
    try:
        frame = con.execute(
            "SELECT ts, open, close FROM candles WHERE interval = '1day' AND scrip_code = ? "
            "ORDER BY ts",
            [eh.NIFTY],
        ).df()
    finally:
        con.close()
    frame["day"] = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    return frame.drop_duplicates("day").set_index("day")[["open", "close"]]


def drift_calls() -> pd.DataFrame:
    """The live rule's calls with the reaction day, using drift_runner's own exit and charges."""

    p = eh.load_panels(Path(dr.DB))
    close, opening, volume = p["close"], p["open"], p["volume"]
    high = dr.mr.load(Path(dr.DB))["high"].reindex(index=close.index, columns=close.columns)
    turnover = (close * volume).rolling(20).mean().shift(1)
    cm = dr._cost_model()
    column = {c: j for j, c in enumerate(close.columns)}
    opens, highs, closes = opening.to_numpy(), high.to_numpy(), close.to_numpy()
    dates = list(close.index)
    rows = []
    events = edv.events(p)
    for code, i1 in zip(events["code"], events["i1"], strict=True):
        j, e = column[code], int(i1) + 1
        if e >= len(dates) or not (math.isfinite(opens[e, j]) and opens[e, j] > 0):
            continue
        parts = dr.legs("live", opens[:, j], highs[:, j], closes[:, j], e)
        if parts is None:
            continue
        x, price, _, _ = parts[0]
        entry = float(opens[e, j])
        net = price / entry - 1 - dr._delivery(cm, entry, price, dr.SLOT_INR)
        span = close.iloc[x] / opening.iloc[e] - 1
        market = float(span[(turnover.iloc[e] >= dr.MIN_MARKET_TURNOVER) & span.notna()].mean())
        rows.append(
            {"reaction": dates[int(i1)], "day": dates[e], "net": net, "excess": net - market}
        )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", nargs="?", default="study", choices=("fetch", "study"))
    args = parser.parse_args(argv)
    nifty = _nifty()
    sessions = list(nifty.index)
    if args.command == "fetch":
        print(json.dumps(fetch(sessions)), flush=True)
    shares = read_table()
    cover = coverage(shares, sessions)
    stale = stale_days()
    print(json.dumps(cover), "stale days:", stale, flush=True)
    if args.command == "fetch":
        return
    stale_set = {date.fromisoformat(d) for d in stale}
    shares = shares.reindex([d for d in sessions if d in shares.index and d not in stale_set])
    nifty = nifty.loc[[d for d in sessions if d >= shares.index[0]]]
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS, "coverage": cover,
        "stale_days": stale,
        "variants": {}, "drift_filter": {},
    }  # fmt: skip
    for rule in RULES:
        signal = rule_signal(rule, shares)
        for hold in HOLDS:
            key = f"{rule}_{hold}"
            report["variants"][key] = judge(trades(signal, nifty, hold))
            print(key, json.dumps(report["variants"][key], default=str), flush=True)
    calls = drift_calls()
    for name, party, flip in (("fii", "FII", False), ("client", "Client", True)):
        rank = trailing_rank(shares[party])
        at = calls["reaction"].map(rank)
        known = calls[at.notna()].reset_index(drop=True)
        values = at.dropna().reset_index(drop=True)
        bad = values >= 2 / 3 if flip else values <= 1 / 3
        report["drift_filter"][name] = judge_filter(known, bad)
        print("drift", name, json.dumps(report["drift_filter"][name], default=str), flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
