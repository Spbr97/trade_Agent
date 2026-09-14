"""Search for an entry rule that actually beats random timing.

Context (measured 2026-09-13, see CLAUDE.md's findings block): the three live setups are
WORSE than picking a random day on the same stock - by ~0.24R at a 2R target. Gross
expectancy is pinned near -0.20R across all 36 exit policies tested, so exits are not the
problem and no ML filter on top can fix a signal source that is anti-predictive. The
question this script asks is the prior one: is there ANY entry rule here that beats the
null?

Method, deliberately identical for every rule so they are comparable:
  - rule is evaluated on bar i's CLOSE (only columns known by then)
  - entry at bar i+1's OPEN, stop 2 x ATR(i) below it, target 2R, max_hold 10 sessions
  - the same gap-aware, stop-wins-ties barrier the labeller and backtester use
  - liquidity floor from config/universe.yaml so fills stay plausible

Discipline that matters more than any rule here: the window is split CHRONOLOGICALLY and
every rule is scored on both halves separately, against a random-timing null computed on
that same half. Testing ~15 rules against 1.9M stock-days will always turn up something
that looks good in-sample - this project has already caught itself doing exactly that once
(the base_breakout "sweet spot" that evaporated out of sample). Only the TEST column and
its edge-vs-null mean anything; the TRAIN column is shown to make overfitting visible
rather than to be believed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore

app = typer.Typer(add_completion=False)

STOP_ATR = 2.0
TARGET_R = 2.0
MAX_HOLD = 10

Rule = Callable[[pd.DataFrame], "pd.Series[bool]"]


def _uptrend(f: pd.DataFrame) -> pd.Series[bool]:
    return f["close"] > f["ema200"]


def _down_days(f: pd.DataFrame, n: int) -> pd.Series[bool]:
    d = f["close"] < f["close"].shift(1)
    return d.rolling(n).sum() == n


RULES: dict[str, Rule] = {
    # --- mean reversion (where the null test points) ---
    "rsi2<10 & above ema200": lambda f: (f["rsi2"] < 10) & _uptrend(f),
    "rsi2<5 & above ema200": lambda f: (f["rsi2"] < 5) & _uptrend(f),
    "rsi2<10 & above ema50": lambda f: (f["rsi2"] < 10) & (f["close"] > f["ema50"]),
    "rsi14<30 & above ema200": lambda f: (f["rsi14"] < 30) & _uptrend(f),
    "below ema10 & above ema50": lambda f: (f["close"] < f["ema10"]) & (f["close"] > f["ema50"]),
    "2 down days & above ema50": lambda f: _down_days(f, 2) & (f["close"] > f["ema50"]),
    "3 down days & above ema50": lambda f: _down_days(f, 3) & (f["close"] > f["ema50"]),
    "3 down days & above ema200": lambda f: _down_days(f, 3) & _uptrend(f),
    "20d low & above ema200": lambda f: (f["close"] <= f["low"].rolling(20).min()) & _uptrend(f),
    "gap down >1atr & above ema200": lambda f: (
        (f["open"] < f["close"].shift(1) - f["atr14"]) & _uptrend(f)
    ),
    # --- momentum / breakout controls (expected to lose, per the null test) ---
    "20d high breakout": lambda f: f["close"] >= f["high"].rolling(20).max(),
    "52w high breakout": lambda f: f["close"] >= f["high52w"],
    "rsi14>70": lambda f: f["rsi14"] > 70,
    # --- trend-quality controls ---
    "above ema200 only": _uptrend,
    "adx>25 & above ema200": lambda f: (f["adx14"] > 25) & _uptrend(f),
}


def _barrier(
    o: Any, h: Any, low_: Any, c: Any, start: int, *, entry: float, stop: float, target: float
) -> float | None:
    """Gross R from `start` onward. Mirrors labeling.py's rules: gap below stop fills at the
    open, a bar touching both stop and target counts as the stop, vertical barrier exits at
    the close."""
    risk = entry - stop
    if risk <= 0:
        return None
    n = min(len(c) - start, MAX_HOLD + 1)
    if n <= 0:
        return None
    for k in range(n):
        i = start + k
        if o[i] <= stop:
            return (float(o[i]) - entry) / risk
        if low_[i] <= stop:
            return -1.0
        if h[i] >= target:
            return TARGET_R
    return (float(c[start + n - 1]) - entry) / risk


def _collect(
    recs: list[dict[str, Any]], arrays: tuple[Any, ...], idxs: Any, tag: str,
    last_entry: int, dsplit: date,
) -> None:  # fmt: skip
    """Simulate one entry per index in `idxs` at the shared comparison geometry. Takes the
    arrays explicitly rather than closing over the per-code loop, so there is no chance of a
    later call silently reading a different code's bars."""
    o, h, low_, c, atr, dates = arrays
    for i in idxs:
        if i >= last_entry or i < 1:
            continue
        entry, a = float(o[i + 1]), float(atr[i])
        if not np.isfinite(entry) or not np.isfinite(a) or a <= 0 or entry <= 0:
            continue
        stop = entry - STOP_ATR * a
        r = _barrier(
            o, h, low_, c, i + 1,
            entry=entry, stop=stop, target=entry + TARGET_R * (entry - stop),
        )  # fmt: skip
        if r is None:
            continue
        recs.append(
            {
                "rule": tag,
                "half": "train" if dates[i] < dsplit else "test",
                "gross_r": r,
                "hit": int(r >= TARGET_R),
            }
        )


@app.command()
def search(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    start: str = typer.Option("2023-09-01", "--from"),
    end: str = typer.Option("2026-09-13", "--to"),
    split: str = typer.Option("2025-03-01", "--split", help="train < split <= test"),
    max_codes: int = typer.Option(0, "--max-codes", help="0 = all"),
    seed: int = typer.Option(20260101, "--seed"),
    out: Path = typer.Option(Path("data/reports/entry_search.csv"), "--out"),
) -> None:
    from datetime import datetime

    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.engine.indicators import daily_features

    settings = load_config(root)
    min_turnover = float(settings.universe.min_avg_daily_turnover_inr)
    min_price = float(settings.universe.min_price)
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    dsplit = datetime.strptime(split, "%Y-%m-%d").date()
    rng = np.random.default_rng(seed)

    recs: list[dict[str, Any]] = []
    with CandleStore(db) as store:
        codes = store.codes(Interval.D1)
        if max_codes:
            codes = codes[:max_codes]
        typer.echo(f"scanning {len(codes)} codes {d0} -> {d1} (split {dsplit})")
        for n_done, code in enumerate(codes, 1):
            if n_done % 400 == 0:
                typer.echo(f"  {n_done}/{len(codes)}")
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            f = daily_features(raw)
            f["_d"] = pd.DatetimeIndex(f.index).tz_convert("Asia/Kolkata").date
            # liquidity + price floor, both as-of each bar (rolling, never forward-looking)
            turnover = (f["close"] * f["volume"]).rolling(20).mean()
            tradeable = (turnover >= min_turnover) & (f["close"] >= min_price)
            in_win = (f["_d"] >= d0) & (f["_d"] <= d1)
            base = tradeable & in_win & f["atr14"].notna() & f["ema200"].notna()
            if not base.any():
                continue

            arrays = (
                f["open"].to_numpy(float), f["high"].to_numpy(float),
                f["low"].to_numpy(float), f["close"].to_numpy(float),
                f["atr14"].to_numpy(float), f["_d"].to_numpy(),
            )  # fmt: skip
            last_entry = len(arrays[3]) - MAX_HOLD - 2

            for name, pred in RULES.items():
                try:
                    mask = pred(f).fillna(False) & base
                except Exception:  # a rule needing a column this code lacks
                    continue
                _collect(recs, arrays, np.flatnonzero(mask.to_numpy()), name, last_entry, dsplit)

            # matched random-timing null: same code, same eligible bars, same geometry
            elig = np.flatnonzero(base.to_numpy())
            elig = elig[(elig >= 1) & (elig < last_entry)]
            if len(elig):
                take = min(len(elig), 40)
                idxs = rng.choice(elig, size=take, replace=False)
                _collect(recs, arrays, idxs, "~RANDOM NULL", last_entry, dsplit)

    df = pd.DataFrame(recs)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.echo(f"\n{len(df)} simulated entries -> {out}\n")
    _report(df)


def _report(df: pd.DataFrame) -> None:
    """Two baselines, both necessary. The RANDOM NULL is unconditional, so a rule that only
    requires an uptrend would score against it merely for being in an uptrend - which is why
    "above ema200 only" is carried as a second, conditioned baseline. A mean-reversion rule
    has to beat THAT to have shown anything of its own. t is Welch's against the random null
    on the same half; with ~15 rules tested, treat |t| under about 3 as noise."""
    g = df.groupby(["rule", "half"])["gross_r"]
    stats = g.agg(n="size", gross="mean", sd="std").reset_index()
    stats = stats.merge(
        df.groupby(["rule", "half"])["hit"].mean().reset_index().rename(columns={"hit": "hit"}),
        on=["rule", "half"],
    )
    null = stats[stats.rule == "~RANDOM NULL"].set_index("half")
    ctrl = stats[stats.rule == "above ema200 only"].set_index("half")

    rows = []
    for rule, grp in stats[stats.rule != "~RANDOM NULL"].groupby("rule"):
        r = grp.set_index("half")
        rec: dict[str, Any] = {"rule": rule}
        for half in ("train", "test"):
            if half not in r.index or half not in null.index:
                continue
            n_r, m_r, s_r = r.loc[half, "n"], r.loc[half, "gross"], r.loc[half, "sd"]
            n_n, m_n, s_n = null.loc[half, "n"], null.loc[half, "gross"], null.loc[half, "sd"]
            se = float(np.sqrt(s_r**2 / max(n_r, 1) + s_n**2 / max(n_n, 1))) or float("nan")
            rec[f"n_{half}"] = int(n_r)
            rec[f"edge_{half}"] = float(m_r - m_n)
            rec[f"t_{half}"] = float((m_r - m_n) / se) if se == se and se > 0 else float("nan")
            if half in ctrl.index:
                rec[f"vs_trend_{half}"] = float(m_r - ctrl.loc[half, "gross"])
        rows.append(rec)
    res = pd.DataFrame(rows).sort_values("edge_test", ascending=False)

    for half in ("train", "test"):
        if half in null.index:
            typer.echo(
                f"random null {half}: n={int(null.loc[half, 'n'])} "
                f"gross={null.loc[half, 'gross']:+.4f} hit={null.loc[half, 'hit']:.4f}"
            )
    typer.echo("")
    cols = [c for c in ["rule", "n_train", "edge_train", "n_test", "edge_test", "t_test",
                        "vs_trend_test"] if c in res.columns]  # fmt: skip
    typer.echo(res[cols].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    typer.echo(
        "\nedge_* = gross R minus the random null on the SAME half (so market regime is"
        "\ncontrolled). vs_trend_test = the same versus 'above ema200 only', which is the"
        "\nbar a mean-reversion rule must clear to have added anything beyond being long a"
        "\nuptrend. Believe edge_test; edge_train is shown so in-sample-only rules are visible."
    )


def _scan_forward(
    o: Any, h: Any, low_: Any, c: Any, start: int, *, entry: float, stop: float,
    targets: list[float], max_hold: int,
) -> dict[tuple[float, int], float]:  # fmt: skip
    """One forward pass per (entry, stop); returns gross R for every (target_r, hold) cell.
    Same gap/tie/vertical rules as labeling.py - just amortised, since the geometry search
    evaluates dozens of cells off the identical forward window."""
    risk = entry - stop
    out: dict[tuple[float, int], float] = {}
    n = min(len(c) - start, max_hold + 1)
    stop_at = None
    stop_r = -1.0
    hit_at: dict[float, int] = {}
    for k in range(n):
        i = start + k
        if stop_at is None:
            if o[i] <= stop:
                stop_at, stop_r = k, (float(o[i]) - entry) / risk
            elif low_[i] <= stop:
                stop_at, stop_r = k, -1.0
        for tr in targets:
            if tr not in hit_at and h[i] >= entry + tr * risk:
                hit_at[tr] = k
        if stop_at is not None and len(hit_at) == len(targets):
            break
    for tr in targets:
        for hold in HOLDS:
            if hold > max_hold:
                continue
            limit = min(hold, n - 1)
            if limit < 0:
                continue
            s_k = stop_at if stop_at is not None and stop_at <= limit else None
            t_k = hit_at.get(tr)
            t_k = t_k if t_k is not None and t_k <= limit else None
            if s_k is not None and (t_k is None or s_k <= t_k):
                out[(tr, hold)] = stop_r
            elif t_k is not None:
                out[(tr, hold)] = tr
            else:
                out[(tr, hold)] = (float(c[start + limit]) - entry) / risk
    return out


STOPS = [1.0, 1.5, 2.0, 3.0]
TARGETS = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
HOLDS = [2, 3, 5, 10]


@app.command()
def optimize(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    rule: str = typer.Option("rsi2<10 & above ema50", "--rule"),
    start: str = typer.Option("2023-09-01", "--from"),
    end: str = typer.Option("2026-09-13", "--to"),
    split: str = typer.Option("2025-03-01", "--split"),
    risk_pct: float = typer.Option(0.005, "--risk-pct", help="risk per trade, fraction"),
    max_codes: int = typer.Option(900, "--max-codes"),
    out: Path = typer.Option(Path("data/reports/entry_optimize.csv"), "--out"),
) -> None:
    """Geometry search for one entry rule, honestly split.

    The grid is scored on the TRAIN half only; the single best cell by net expectancy is
    then scored ONCE on the held-out test half. Searching 96 cells will always produce a
    flattering in-sample winner - the test column is the only one that means anything, and
    it is looked at exactly once, which is the same discipline train.py's final_test_frac
    reservation enforces for the model."""
    from datetime import datetime
    from decimal import Decimal

    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.engine.indicators import daily_features
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    if rule not in RULES:
        raise typer.BadParameter(f"unknown rule; choose from: {list(RULES)}")
    settings = load_config(root)
    cm = EquityCostModel(settings.risk.costs)
    risk_rupees = float(settings.risk.trading_capital) * risk_pct
    min_turnover = float(settings.universe.min_avg_daily_turnover_inr)
    min_price = float(settings.universe.min_price)
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    dsplit = datetime.strptime(split, "%Y-%m-%d").date()
    pred = RULES[rule]

    recs: list[dict[str, Any]] = []
    with CandleStore(db) as store:
        codes = store.codes(Interval.D1)[:max_codes]
        typer.echo(f"optimizing '{rule}' over {len(codes)} codes, risk/trade {risk_pct:.2%}")
        for n_done, code in enumerate(codes, 1):
            if n_done % 300 == 0:
                typer.echo(f"  {n_done}/{len(codes)}")
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            f = daily_features(raw)
            f["_d"] = pd.DatetimeIndex(f.index).tz_convert("Asia/Kolkata").date
            turnover = (f["close"] * f["volume"]).rolling(20).mean()
            base = (
                (turnover >= min_turnover) & (f["close"] >= min_price)
                & (f["_d"] >= d0) & (f["_d"] <= d1)
                & f["atr14"].notna() & f["ema200"].notna()
            )  # fmt: skip
            try:
                mask = pred(f).fillna(False) & base
            except Exception:
                continue
            if not mask.any():
                continue
            o, h = f["open"].to_numpy(float), f["high"].to_numpy(float)
            low_, c = f["low"].to_numpy(float), f["close"].to_numpy(float)
            atr = f["atr14"].to_numpy(float)
            dates = f["_d"].to_numpy()
            last = len(c) - max(HOLDS) - 2
            for i in np.flatnonzero(mask.to_numpy()):
                if i < 1 or i >= last:
                    continue
                entry, a = float(o[i + 1]), float(atr[i])
                if not (np.isfinite(entry) and np.isfinite(a)) or a <= 0 or entry <= 0:
                    continue
                half = "train" if dates[i] < dsplit else "test"
                for sm in STOPS:
                    stop = entry - sm * a
                    if stop <= 0:
                        continue
                    qty = max(1.0, round(risk_rupees / (entry - stop)))
                    cost_r = float(
                        cm.round_trip_cost(
                            trade_type=TradeType.DELIVERY, qty=qty,
                            entry_price=Decimal(str(round(entry, 2))),
                            exit_price=Decimal(str(round(entry, 2))),
                        ).total
                    ) / (qty * (entry - stop))  # fmt: skip
                    cells = _scan_forward(
                        o, h, low_, c, i + 1, entry=entry, stop=stop,
                        targets=TARGETS, max_hold=max(HOLDS),
                    )  # fmt: skip
                    for (tr, hold), gross in cells.items():
                        recs.append(
                            {"half": half, "stop_atr": sm, "target_r": tr, "hold": hold,
                             "gross_r": gross, "net_r": gross - cost_r}
                        )  # fmt: skip

    df = pd.DataFrame(recs)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    tr_df = df[df.half == "train"]
    agg = tr_df.groupby(["stop_atr", "target_r", "hold"]).agg(
        n=("net_r", "size"), hit=("gross_r", lambda s: float((s > 0).mean())),
        gross=("gross_r", "mean"), net=("net_r", "mean"),
    ).reset_index()  # fmt: skip
    agg = agg[agg.n >= 300].sort_values("net", ascending=False)
    typer.echo(f"\n{len(df)} cells simulated -> {out}")
    typer.echo(f"\nTRAIN half, top 12 geometries for '{rule}':")
    typer.echo(agg.head(12).to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    if agg.empty:
        typer.echo("\nno cell had enough samples")
        return
    best = agg.iloc[0]
    te = df[
        (df.half == "test") & (df.stop_atr == best.stop_atr)
        & (df.target_r == best.target_r) & (df.hold == best.hold)
    ]  # fmt: skip
    typer.echo(
        f"\n=== LOCKED TEST, scored once: stop {best.stop_atr}xATR, target {best.target_r}R, "
        f"hold {best.hold} ==="
    )
    if te.empty:
        typer.echo("no test samples for that cell")
        return
    typer.echo(
        f"  train: n={int(best.n):>6} gross={best.gross:+.4f} net={best.net:+.4f}\n"
        f"  TEST : n={len(te):>6} gross={te.gross_r.mean():+.4f} net={te.net_r.mean():+.4f} "
        f"win_rate={(te.gross_r > 0).mean():.4f}"
    )
    typer.echo(
        "\nnet is after real NSE round-trip costs at the given --risk-pct. A positive TEST"
        "\nnet is the only thing here that would justify building a setup on this rule."
    )


@app.command()
def report(path: Path = typer.Option(Path("data/reports/entry_search.csv"), "--path")) -> None:
    _report(pd.read_csv(path))


if __name__ == "__main__":
    app()
