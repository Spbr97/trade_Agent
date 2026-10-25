"""Fair random-entry benchmark for multi-session (daily-bar) trades.

Why this exists: `backtest/null_baseline.py::run_null_baseline` was built for INTRADAY setups
(random entries inside the same session, closed by its end). For daily candidates the lab
(`run_lab_candidate.to_harness_trades`) passes `session_start = entry_bar` and
`session_end = exit_bar`, which makes the random side systematically worse than random:

1. random entries are drawn only inside the real trade's own holding window;
2. they are force-closed at the real trade's exit bar - for a trailing exit, the bar just
   after the pullback that fired the trail, i.e. a level chosen with hindsight;
3. they use a fixed `t2` target instead of the real exit plan;
4. late draws get one or two bars to work;
5. its net-R line prices costs as INTRADAY, which skips delivery STT and the DP charge.

On NSE 2017-2026 this put random entries near -0.09R gross where a fair draw (same exit,
no forced close) earns about +0.05 to +0.08R (docs/fair-random-benchmark.md).

Method here, for each real trade:

- Pool = every ELIGIBLE bar of the same stock within +/- `window_sessions` of the real entry
  bar. Eligibility is the caller's mask (a candidate's trend filter, say) and never depends
  on when, or how, the real trade exited.
- Every pool bar is simulated ONCE with the real backtester's `evaluate_entry` /
  `evaluate_exit`, using a signal with the real trade's geometry: stop distance in ATR units,
  T1/T2 as multiples of risk, the same exit plan, entry validity and chase limit. Like
  `run_lab_candidate.simulate_single_setup`, the fill bar is not exit-evaluated and a
  position still open when data ends is dropped (never marked to a chosen close).
- Cohort j = the mean, across trades, of one random draw from each trade's pool. Because
  outcomes are cached per (stock, bar, geometry), 20,000 cohorts are cheap, so
  p = (1 + #cohorts >= setup mean) / (n_cohorts + 1) can resolve 0.00005 - small enough
  for a Bonferroni threshold across hundreds of search trials.
- Gross R is compared, as in the original. Optional net figures price DELIVERY costs.

Research only: nothing here alerts, sizes or trades, and no production module is changed.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.backtest.fills import Bar, EntryOutcome, Position, evaluate_entry, evaluate_exit

SLIPPAGE_PCT = 0.0005  # same as run_lab_candidate.SLIPPAGE_PCT
SIM_QTY = 1000.0  # large enough that a 50% partial is never rounded into the whole lot
DEFAULT_COHORTS = 20_000
DEFAULT_WINDOW = 126  # sessions either side of the real entry (about six months)
DEFAULT_SEED = 20261025
REQUIRED_COLUMNS = ("open", "high", "low", "close", "ema10", "atr14")


@dataclass(frozen=True)
class FairTrade:
    """One real trade: where it filled and the signal that armed it (for its geometry)."""

    scrip_code: str
    entry_bar: int  # positional index into the frame, the bar the entry filled on
    signal: Any  # LabSignal or production Signal: trigger, stop, t1, t2, atr, exit_plan, ...
    gross_r: float
    net_r: float | None = None  # the real trade's own net R, if already costed


@dataclass
class FairNullResult:
    n_trades: int
    n_used: int  # trades with a non-empty random pool
    n_cohorts: int
    setup_mean_gross_r: float
    null_mean_gross_r: float
    null_std_gross_r: float
    margin_gross_r: float  # setup minus null
    p_value: float
    p_resolution: float  # smallest p this many cohorts can show
    passes: bool
    alpha: float
    median_pool_size: float
    setup_mean_net_r: float | None = None
    null_mean_net_r: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _reshape(sig: Any, *, trigger: float, stop: float, t1: float, t2: float, atr: float) -> Any:
    update = {"trigger": trigger, "stop": stop, "t1": t1, "t2": t2, "atr": atr}
    if dataclasses.is_dataclass(sig):
        return dataclasses.replace(sig, **update)
    return sig.model_copy(update=update)  # pydantic production Signal


def _geometry(sig: Any) -> tuple[float, float, float] | None:
    risk = float(sig.trigger) - float(sig.stop)
    atr = float(sig.atr)
    if not (risk > 0 and atr > 0):
        return None
    return (
        round(risk / atr, 6),
        round((float(sig.t1) - float(sig.trigger)) / risk, 6),
        round((float(sig.t2) - float(sig.trigger)) / risk, 6),
    )


def _geometry_key(sig: Any, geo: tuple[float, float, float]) -> tuple[Any, ...]:
    plan = sig.exit_plan
    plan_key = tuple(sorted(plan.model_dump().items())) if hasattr(plan, "model_dump") else plan
    return (geo, plan_key, int(sig.valid_sessions), float(sig.chased_atr_mult))


class _Frame:
    """Plain arrays for one stock: a pandas row lookup per bar is the dominant cost."""

    def __init__(self, frame: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"frame lacks columns {missing}")
        self.cols = {c: frame[c].to_numpy(float) for c in REQUIRED_COLUMNS}
        self.dates: list[date] = [pd.Timestamp(ts).date() for ts in frame.index]
        self.n = len(frame)

    def bar(self, i: int) -> Bar:
        c = self.cols
        return Bar(
            on=self.dates[i], open=c["open"][i], high=c["high"][i], low=c["low"][i],
            close=c["close"][i], volume=0, ema10=c["ema10"][i], atr=c["atr14"][i],
        )  # fmt: skip


def simulate_random_entry(
    fr: _Frame, arm_bar: int, template: Any, geo: tuple[float, float, float],
    *, slippage_pct: float = SLIPPAGE_PCT,
) -> tuple[float, float, float, float] | None:  # fmt: skip
    """Arm a signal with `template`'s geometry at the close of `arm_bar`, then fill and
    manage it exactly like `simulate_single_setup`. Returns (gross_r, entry, stop, exit)
    or None when it never fills, is chased/invalidated, or is still open when data ends."""

    atr = fr.cols["atr14"][arm_bar]
    trigger = fr.cols["close"][arm_bar]
    if not (np.isfinite(atr) and atr > 0 and np.isfinite(trigger) and trigger > 0):
        return None
    k_stop, m1, m2 = geo
    risk = k_stop * atr
    sig = _reshape(
        template, trigger=trigger, stop=trigger - risk, t1=trigger + m1 * risk,
        t2=trigger + m2 * risk, atr=atr,
    )  # fmt: skip
    if sig.stop <= 0:
        return None
    position: Position | None = None
    i = arm_bar + 1
    waited = 0
    while i < fr.n and position is None:
        outcome, price = evaluate_entry(sig, fr.bar(i), slippage_pct)
        if outcome == EntryOutcome.FILLED and price is not None:
            position = Position(
                signal=sig, entry_date=fr.dates[i], entry_price=price,
                qty_initial=SIM_QTY, qty_open=SIM_QTY, stop=sig.stop,
            )  # fmt: skip
        elif outcome in (EntryOutcome.CHASED, EntryOutcome.INVALIDATED):
            return None
        else:
            waited += 1
            if waited >= sig.valid_sessions:
                return None
        i += 1
    if position is None:
        return None
    while i < fr.n and not position.closed:  # the fill bar itself is not exit-evaluated
        evaluate_exit(position, fr.bar(i), slippage_pct, entry_day=False)
        i += 1
    if not position.closed or position.initial_risk <= 0:
        return None
    exit_px = position.fills[-1].price
    return (
        position.gross_pnl() / position.initial_risk,
        position.entry_price,
        float(sig.stop),
        exit_px,
    )


def _net_r(cost_model: Any, entry: float, stop: float, exit_px: float, gross_r: float,
           risk_rupees: float) -> float:  # fmt: skip
    from tradedesk.models import TradeType

    per_share = entry - stop
    qty = max(1.0, round(risk_rupees / per_share))
    cost = cost_model.round_trip_cost(
        trade_type=TradeType.DELIVERY, qty=qty,
        entry_price=Decimal(str(round(entry, 2))), exit_price=Decimal(str(round(exit_px, 2))),
    )  # fmt: skip
    return gross_r - float(cost.total) / (qty * per_share)


def point_in_time_members(frames: Mapping[str, pd.DataFrame], top_n: int,
                          *, lookback: int = 20) -> dict[str, np.ndarray]:  # fmt: skip
    """For each stock, a boolean array: was it among the `top_n` most liquid of the loaded
    stocks ON THAT DAY (mean close x volume over the prior `lookback` sessions)? Picking a
    universe by liquidity at the END of the history keeps the stocks that rose; judging
    trades and random entries only while a stock was in the point-in-time universe removes
    most of that hindsight (the loaded set itself can still carry some survivorship)."""

    turnover = {}
    for code, f in frames.items():
        tv = (f["close"] * f["volume"]).rolling(lookback, min_periods=lookback).mean().shift(1)
        turnover[code] = tv
    panel = pd.DataFrame(turnover)
    rank = panel.rank(axis=1, ascending=False, method="first")
    member = (rank <= top_n) & panel.notna()
    return {
        code: member[code].reindex(frames[code].index).fillna(False).to_numpy(bool)
        for code in frames
    }


def run_fair_null(
    trades: Sequence[FairTrade],
    frames: Mapping[str, pd.DataFrame],
    *,
    eligible: Mapping[str, np.ndarray] | None = None,
    window_sessions: int = DEFAULT_WINDOW,
    max_pool: int | None = 80,
    n_cohorts: int = DEFAULT_COHORTS,
    alpha: float = 0.05,
    seed: int = DEFAULT_SEED,
    cost_model: Any | None = None,
    risk_rupees: float = 500.0,
) -> FairNullResult:
    """Compare the real trades' mean gross R with matched random entries drawn fairly
    (see module docstring). `eligible[code]` is a boolean array aligned with `frames[code]`;
    without it every bar with a positive ATR is eligible. `max_pool` caps how many pool bars
    are simulated per trade (sampled at random) to bound run time; None simulates them all."""

    if not trades:
        raise ValueError("no trades to validate")
    rng = np.random.default_rng(seed)
    cache: dict[tuple[Any, ...], tuple[float, float, float, float] | None] = {}
    prepared: dict[str, _Frame] = {}
    pools_g: list[np.ndarray] = []
    pools_n: list[np.ndarray] = []
    used_r: list[float] = []
    used_net: list[float] = []
    notes: list[str] = []
    for t in trades:
        frame = frames.get(t.scrip_code)
        geo = _geometry(t.signal)
        if frame is None or geo is None:
            continue
        fr = prepared.get(t.scrip_code)
        if fr is None:
            fr = prepared[t.scrip_code] = _Frame(frame)
        lo, hi = max(0, t.entry_bar - window_sessions), min(fr.n - 2, t.entry_bar + window_sessions)
        cand = np.arange(lo, hi + 1)
        mask = eligible.get(t.scrip_code) if eligible is not None else None
        if mask is not None:
            cand = cand[np.asarray(mask, bool)[cand]]
        else:
            atr = fr.cols["atr14"][cand]
            cand = cand[np.isfinite(atr) & (atr > 0)]
        if max_pool is not None and len(cand) > max_pool:
            cand = rng.choice(cand, size=max_pool, replace=False)
        gkey = _geometry_key(t.signal, geo)
        gross, net = [], []
        for b in cand:
            key = (t.scrip_code, int(b), gkey)
            if key not in cache:
                cache[key] = simulate_random_entry(fr, int(b), t.signal, geo)
            res = cache[key]
            if res is None:
                continue
            gross.append(res[0])
            if cost_model is not None:
                net.append(_net_r(cost_model, res[1], res[2], res[3], res[0], risk_rupees))
        if not gross:
            continue
        pools_g.append(np.array(gross))
        pools_n.append(np.array(net))
        used_r.append(t.gross_r)
        if t.net_r is not None:
            used_net.append(t.net_r)
        elif cost_model is not None:
            sig = t.signal
            entry = float(sig.trigger)
            exit_px = entry + t.gross_r * (entry - float(sig.stop))
            used_net.append(_net_r(cost_model, entry, float(sig.stop), exit_px, t.gross_r,
                                   risk_rupees))  # fmt: skip
    if not pools_g:
        raise ValueError("no trade had a usable random-entry pool")
    n_used = len(pools_g)
    if n_used < len(trades):
        notes.append(f"{len(trades) - n_used} trades had no usable pool and were left out")
    cohorts = np.zeros(n_cohorts)
    cohorts_net = np.zeros(n_cohorts)
    for g, nn in zip(pools_g, pools_n, strict=True):
        idx = rng.integers(0, len(g), size=n_cohorts)
        cohorts += g[idx] / n_used
        if cost_model is not None:
            cohorts_net += nn[idx] / n_used
    setup_mean = float(np.mean(used_r))
    p = float((1 + np.sum(cohorts >= setup_mean)) / (n_cohorts + 1))
    null_mean = float(cohorts.mean())
    return FairNullResult(
        n_trades=len(trades),
        n_used=n_used,
        n_cohorts=n_cohorts,
        setup_mean_gross_r=setup_mean,
        null_mean_gross_r=null_mean,
        null_std_gross_r=float(cohorts.std()),
        margin_gross_r=setup_mean - null_mean,
        p_value=p,
        p_resolution=1.0 / (n_cohorts + 1),
        passes=p < alpha,
        alpha=alpha,
        median_pool_size=float(np.median([len(g) for g in pools_g])),
        setup_mean_net_r=float(np.mean(used_net)) if len(used_net) == n_used else None,
        null_mean_net_r=float(cohorts_net.mean()) if cost_model is not None else None,
        notes=notes,
    )
