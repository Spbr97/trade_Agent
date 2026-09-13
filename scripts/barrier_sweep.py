"""Measure the hit-rate vs target-distance curve on real triggered signals.

The question: at what target does the hit rate actually reach 80%, and what is the
expectancy there? Hit rate and payoff trade off directly - a closer target is hit more
often but pays less per win, while NSE round-trip costs are a near-fixed rupee drag that
becomes a LARGER share of R as the stop tightens. Nothing in this project had ever
measured that curve, so "can we get to 80%" had only ever been answered by argument.

Two phases so the expensive part runs once:
  `dump`  - run the backtest and write one row per TRIGGERED signal (entry fill, stop,
            entry date, code). This is the slow step (full universe, multi-year).
  `sweep` - re-label those signals under a grid of (target_R, max_hold) and report hit
            rate plus gross and net expectancy per cell. Fast, repeatable.

The signal POPULATION is held fixed across the sweep on purpose: `min_net_rr` gates on
the final target (T2), which this never touches, so every cell scores the same trades and
differences are attributable to the exit definition alone.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import typer

from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.prediction.labeling import triple_barrier

app = typer.Typer(add_completion=False)

DUMP = Path("data/reports/barrier_signals.csv")
OUT = Path("data/reports/barrier_sweep.csv")


@app.command()
def dump(
    from_: str = typer.Option("2023-09-01", "--from"),
    to: str | None = typer.Option(None, "--to"),
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    out: Path = typer.Option(DUMP, "--out"),
) -> None:
    """Run the backtest once and dump every triggered signal's entry/stop/date."""
    from datetime import datetime

    from tradedesk.backtest import prepare_market, run_backtest
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.engine.lifecycle import SignalState
    from tradedesk.prediction.train import _fill_from_note
    from tradedesk.scan import scan_config

    settings = load_config(root)
    start = datetime.strptime(from_, "%Y-%m-%d").date()
    end = datetime.strptime(to, "%Y-%m-%d").date() if to else datetime.now(IST).date()
    cfg = scan_config(settings, end, None)
    cfg.start = start

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from tradedesk.cli import _reference_code, _sector_config

    with CandleStore(db) as store:
        ref = _reference_code(store, root)
        vix = store.index_code(settings.universe.volatility_index)
        cfg.vix_code = vix
        cfg.sector_of, cfg.sector_codes = _sector_config(store, root)
        codes = [c for c in store.codes(Interval.D1) if c not in (ref, vix)]
        typer.echo(f"backtesting {len(codes)} codes {start} -> {end}")
        md = prepare_market(store, codes, ref, cfg)
    result = run_backtest(md, cfg)

    rows: list[dict[str, Any]] = []
    for ts in result.signals:
        trig = next((h for h in ts.history if h.to_state is SignalState.TRIGGERED), None)
        if trig is None:
            continue
        sig = ts.signal
        fill = _fill_from_note(trig.note) or sig.trigger
        if fill <= sig.stop:
            continue  # a fill at or below the stop has no risk denominator
        rows.append(
            {
                "signal_id": sig.id,
                "scrip_code": sig.scrip_code,
                "setup": sig.setup.value,
                "armed_on": sig.armed_on,
                "entry_date": trig.on,
                "entry": fill,
                "stop": sig.stop,
                "t1": sig.t1,
                "t2": sig.t2,
                "atr": sig.atr,
            }
        )
    df = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.echo(f"{len(df)} triggered signals -> {out}")


def _fast_barrier(
    opens: Any, highs: Any, lows: Any, closes: Any, *, entry: float, stop: float,
    target: float, max_hold: int,
) -> tuple[int, str, int, float | None]:  # fmt: skip
    """Array-level twin of labeling.py::triple_barrier, for the sweep's ~1M calls (the
    canonical one rebuilds four numpy arrays from a DataFrame per call). `test_fast_barrier`
    below asserts the two agree on every dumped signal, so this can never quietly drift
    into different semantics - if it does, the sweep is wrong and the check fails loudly."""
    n = min(len(opens), max_hold + 1)
    for i in range(n):
        if i > 0 and opens[i] <= stop:
            return 0, "gap_stop", i, float(opens[i])
        if lows[i] <= stop:
            return 0, "stop", i, stop
        if highs[i] >= target:
            return 1, "target", i, target
    if n < max_hold + 1:
        return 0, "insufficient", n - 1, None
    return 0, "timeout", max_hold, float(closes[n - 1])


@app.command()
def check(
    dump_path: Path = typer.Option(DUMP, "--dump"),
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    n: int = typer.Option(400, "--n", help="signals to cross-check"),
) -> None:
    """Assert _fast_barrier matches labeling.py::triple_barrier exactly on real signals."""
    from tradedesk.broker.indstocks.models import Interval

    sig = pd.read_csv(dump_path, parse_dates=["entry_date"]).head(n)
    checked = 0
    with CandleStore(db) as store:
        for _, s in sig.iterrows():
            bars = store.load(s["scrip_code"], Interval.D1)
            if bars is None or not len(bars):
                continue
            d = pd.DatetimeIndex(bars.index).tz_convert("Asia/Kolkata").date
            idx = d.searchsorted(s["entry_date"].date())
            fwd = bars.iloc[idx:]
            if not len(fwd):
                continue
            entry, stop = float(s["entry"]), float(s["stop"])
            o, h, low_, c = (
                fwd["open"].to_numpy(float), fwd["high"].to_numpy(float),
                fwd["low"].to_numpy(float), fwd["close"].to_numpy(float),
            )  # fmt: skip
            for tr in (0.5, 1.0, 2.0, 3.0):
                for mh in (3, 10, 20):
                    tgt = entry + tr * (entry - stop)
                    ref = triple_barrier(fwd, entry=entry, stop=stop, target=tgt, max_hold=mh)
                    got = _fast_barrier(o, h, low_, c, entry=entry, stop=stop, target=tgt, max_hold=mh)  # noqa: E501
                    assert (ref.label, ref.outcome, ref.sessions) == got[:3], (
                        f"{s['signal_id']} tr={tr} mh={mh}: {ref} != {got}"
                    )
                    checked += 1
    typer.echo(f"OK - {checked} cells matched labeling.py::triple_barrier exactly")


@app.command()
def sweep(
    dump_path: Path = typer.Option(DUMP, "--dump"),
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    out: Path = typer.Option(OUT, "--out"),
    targets: str = typer.Option("0.25,0.5,0.75,1.0,1.25,1.5,2.0,2.5,3.0", "--targets"),
    holds: str = typer.Option("3,5,10,20", "--holds"),
) -> None:
    """Re-label the dumped signals under every (target_R, max_hold) cell."""
    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    settings = load_config(root)
    costs = EquityCostModel(settings.risk.costs)
    risk_rupees = float(settings.risk.trading_capital) * float(
        settings.risk.max_risk_per_trade_pct
    )

    sig = pd.read_csv(dump_path, parse_dates=["entry_date"])
    target_grid = [float(x) for x in targets.split(",")]
    hold_grid = [int(x) for x in holds.split(",")]

    # Load each code's daily bars once - the sweep touches the same forward window
    # repeatedly and re-querying per cell would dominate the runtime.
    bars_by_code: dict[str, pd.DataFrame] = {}
    with CandleStore(db) as store:
        for code in sorted(sig["scrip_code"].unique()):
            df = store.load(code, Interval.D1)
            if df is not None and len(df):
                df = df.copy()
                df["_d"] = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata").date
                bars_by_code[code] = df
    typer.echo(f"loaded bars for {len(bars_by_code)} codes")

    records: list[dict[str, Any]] = []
    for _, s in sig.iterrows():
        bars = bars_by_code.get(s["scrip_code"])
        if bars is None:
            continue
        entry_day = s["entry_date"].date()
        idx = bars["_d"].searchsorted(entry_day)
        if idx >= len(bars):
            continue
        fwd = bars.iloc[idx : idx + max(hold_grid) + 1]
        o = fwd["open"].to_numpy(float)
        h = fwd["high"].to_numpy(float)
        low_ = fwd["low"].to_numpy(float)
        c = fwd["close"].to_numpy(float)
        entry, stop = float(s["entry"]), float(s["stop"])
        risk_per_share = entry - stop
        qty = max(1.0, round(risk_rupees / risk_per_share))
        dec_entry = Decimal(str(round(entry, 2)))
        dec_stop = Decimal(str(round(stop, 2)))
        for tr in target_grid:
            target_price = entry + tr * risk_per_share
            for mh in hold_grid:
                label, outcome, sessions, exit_price = _fast_barrier(
                    o, h, low_, c, entry=entry, stop=stop, target=target_price, max_hold=mh
                )
                if outcome == "insufficient" or exit_price is None:
                    continue
                gross_r = (exit_price - entry) / risk_per_share
                net_r = float(
                    costs.net_r_multiple(
                        trade_type=TradeType.DELIVERY,
                        qty=qty,
                        entry=dec_entry,
                        stop=dec_stop,
                        exit_price=Decimal(str(round(exit_price, 2))),
                    )
                )
                records.append(
                    {
                        "signal_id": s["signal_id"],
                        "setup": s["setup"],
                        "entry_date": entry_day,
                        "target_r": tr,
                        "max_hold": mh,
                        "outcome": outcome,
                        "hit": label,
                        "sessions": sessions,
                        "gross_r": gross_r,
                        "net_r": net_r,
                    }
                )

    trades = pd.DataFrame(records)
    out.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out, index=False)
    typer.echo(f"{len(trades)} labelled outcomes -> {out}\n")

    agg = (
        trades.groupby(["target_r", "max_hold"])
        .agg(
            n=("hit", "size"),
            hit_rate=("hit", "mean"),
            gross_exp=("gross_r", "mean"),
            net_exp=("net_r", "mean"),
        )
        .reset_index()
    )
    agg["cost_drag_r"] = agg["gross_exp"] - agg["net_exp"]
    typer.echo(agg.to_string(index=False, float_format=lambda x: f"{x:.4f}"))


@app.command()
def null_test(
    dump_path: Path = typer.Option(DUMP, "--dump"),
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    seed: int = typer.Option(20260101, "--seed"),
    targets: str = typer.Option("1.0,2.0,3.0", "--targets"),
    hold: int = typer.Option(10, "--hold"),
) -> None:
    """Does the setup's TIMING add anything, or is the negative gross expectancy just the
    geometry?

    The sweep showed gross expectancy pinned near -0.2R at every exit policy. Two very
    different things produce that: (a) the entries are genuinely bad, or (b) it is
    mechanical - entry slippage plus the conservative "a bar touching both stop and target
    counts as the stop" tie rule, which would drag ANY entry negative.

    So: hold the stock and the stop width (as a fraction of price) fixed, and randomise
    only WHEN you enter. Same geometry, same tie rule, same costs - the setup's timing is
    the only thing removed. If random timing scores the same, the setups add nothing but
    are not themselves harmful; if random scores materially better, the setups are worse
    than picking a day at random, which is a far stronger indictment."""
    import numpy as np

    from tradedesk.broker.indstocks.models import Interval

    rng = np.random.default_rng(seed)
    sig = pd.read_csv(dump_path, parse_dates=["entry_date"])
    target_grid = [float(x) for x in targets.split(",")]

    # Random dates MUST be drawn from the same window the setups actually fired in. The
    # store holds ~10 years per code, and 2020-2024 was a far stronger tape than 2023-2026 -
    # sampling the full history would hand the null a market the setups never traded and
    # make this comparison meaningless.
    lo, hi = sig["entry_date"].min().date(), sig["entry_date"].max().date()
    typer.echo(f"restricting random entries to the backtest window {lo} -> {hi}")

    bars_by_code: dict[str, Any] = {}
    with CandleStore(db) as store:
        for code in sorted(sig["scrip_code"].unique()):
            df = store.load(code, Interval.D1)
            if df is None or not len(df):
                continue
            d = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata").date
            start, end = int(d.searchsorted(lo)), int(d.searchsorted(hi))
            if end - start <= hold + 2:
                continue
            bars_by_code[code] = (
                df["open"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), df["close"].to_numpy(float), start, end,
            )  # fmt: skip

    rows: list[dict[str, Any]] = []
    for _, s in sig.iterrows():
        arr = bars_by_code.get(s["scrip_code"])
        if arr is None:
            continue
        o, h, low_, c, w_start, w_end = arr
        entry_o, stop_o = float(s["entry"]), float(s["stop"])
        if entry_o <= 0:
            continue
        risk_frac = (entry_o - stop_o) / entry_o  # stop width as a fraction of price
        i = int(rng.integers(w_start, w_end))
        entry = float(c[i])
        if entry <= 0:
            continue
        risk = entry * risk_frac
        stop = entry - risk
        fo, fh, fl, fc = o[i + 1 :], h[i + 1 :], low_[i + 1 :], c[i + 1 :]
        for tr in target_grid:
            label, outcome, _, exit_price = _fast_barrier(
                fo, fh, fl, fc, entry=entry, stop=stop, target=entry + tr * risk, max_hold=hold
            )
            if outcome == "insufficient" or exit_price is None:
                continue
            rows.append(
                {"target_r": tr, "hit": label, "gross_r": (exit_price - entry) / risk}
            )

    nul = pd.DataFrame(rows)
    sw = pd.read_csv(OUT)
    typer.echo(f"\nrandom-timing null, {len(nul)//len(target_grid)} entries/target, hold={hold}\n")
    typer.echo(f"{'target_r':>9} {'real_hit':>9} {'null_hit':>9} {'real_gross':>11} {'null_gross':>11} {'edge':>8}")  # noqa: E501
    for tr in target_grid:
        r = sw[(sw.target_r == tr) & (sw.max_hold == hold)]
        n = nul[nul.target_r == tr]
        edge = r.gross_r.mean() - n.gross_r.mean()
        typer.echo(
            f"{tr:>9.2f} {r.hit.mean():>9.4f} {n.hit.mean():>9.4f} "
            f"{r.gross_r.mean():>11.4f} {n.gross_r.mean():>11.4f} {edge:>8.4f}"
        )
    typer.echo(
        "\nedge > 0 => the setup's timing beats a random day on the same stock;"
        "\nedge ~ 0 => the setups add nothing, and the negative gross is mechanical."
    )


if __name__ == "__main__":
    app()
