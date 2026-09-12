"""Market regime (PLAN.md 6.1): risk_on / neutral / risk_off from the benchmark's trend,
universe breadth and India VIX. Computed each evening; thresholds live in config/engine.yaml.

| risk_on  | Nifty above a rising EMA50, breadth > 50%, VIX calm      | full size
| neutral  | mixed                                                    | half size, A-grade only
| risk_off | Nifty below EMA50 with breadth < 40%, or a VIX spike     | no new swing entries
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

import pandas as pd
from pydantic import BaseModel, ConfigDict

from tradedesk.config.models import RegimeConfig
from tradedesk.engine.indicators import ema


class Regime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class RegimeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: date
    regime: Regime
    benchmark_close: float
    benchmark_ema: float
    ema_rising: bool
    above_ema: bool
    breadth_pct: float | None
    vix: float | None
    vix_change_5d_pct: float | None
    size_multiplier: float
    reasons: list[str]


def breadth_above_ema(closes: pd.DataFrame, n: int = 50) -> pd.Series:
    """Percent of columns (stocks) whose close is above their own EMA(n), per row (date).
    `closes`: wide frame, index = date, columns = scrip codes; NaN where not listed."""
    above = closes > closes.apply(lambda s: ema(s.dropna(), n).reindex(s.index))
    listed = closes.notna()
    return above.sum(axis=1) / listed.sum(axis=1).replace(0, pd.NA) * 100


def classify_regime(
    benchmark: pd.DataFrame,
    *,
    breadth_pct: float | None,
    vix: pd.Series | None,
    cfg: RegimeConfig,
    on: date | None = None,
    vix_required: bool = False,
) -> RegimeSnapshot:
    """Classify using the last bar of `benchmark` (daily OHLCV) and the latest breadth/VIX.

    Only the information available at that close is used; callers must slice inputs to the
    evaluation date before calling (look-ahead test enforces this).

    `vix_required` (Market.vix_required: True for NSE, False for crypto) says whether a
    missing `vix` is a DATA FAILURE for this market, as opposed to a fact about it (crypto
    has no VIX). Without this distinction, `vix_calm = vix_last is None or ...` below
    treats "no data" the same as "confirmed calm" - which is how NSE's regime silently ran
    without its volatility input for the project's whole history (a case-mismatched lookup
    left vix_code unresolved, no error anywhere). CLAUDE.md's hard rule is explicit: "Fail
    closed: stale data ... pause alerts" - so when this market is supposed to have a VIX
    reading and does not, the regime fails to RISK_OFF rather than falling through to a
    classification that never checked the one thing it was told it must check.
    """
    close = benchmark["close"]
    e = ema(close, cfg.nifty_ema)
    last_close = float(close.iloc[-1])
    last_ema = float(e.iloc[-1])
    lb = cfg.ema_slope_lookback
    rising = bool(len(e) > lb and e.iloc[-1] > e.iloc[-1 - lb])
    above = last_close > last_ema
    when = on or pd.Timestamp(benchmark.index[-1]).date()

    vix_last: float | None = None
    vix_chg: float | None = None
    if vix is not None and len(vix.dropna()) > 0:
        v = vix.dropna()
        vix_last = float(v.iloc[-1])
        if len(v) > 5:
            vix_chg = (vix_last / float(v.iloc[-6]) - 1) * 100

    if vix_required and vix_last is None:
        return RegimeSnapshot(
            on=when,
            regime=Regime.RISK_OFF,
            benchmark_close=last_close,
            benchmark_ema=last_ema,
            ema_rising=rising,
            above_ema=above,
            breadth_pct=breadth_pct,
            vix=None,
            vix_change_5d_pct=None,
            size_multiplier=0.0,
            reasons=["VIX data missing for a market that requires it - failing closed"],
        )

    reasons: list[str] = []
    vix_spike = vix_last is not None and (
        vix_last >= float(cfg.vix_spike_level)
        or (vix_chg is not None and vix_chg >= float(cfg.vix_spike_change_5d_pct))
    )
    vix_calm = vix_last is None or vix_last <= float(cfg.vix_calm_max)
    weak_breadth = breadth_pct is not None and breadth_pct < float(cfg.breadth_risk_off_pct)
    strong_breadth = breadth_pct is not None and breadth_pct > float(cfg.breadth_risk_on_pct)

    if vix_spike:
        reasons.append(f"VIX spike ({vix_last:.1f}, 5d {vix_chg or 0:+.0f}%)")
    if not above:
        reasons.append(f"benchmark below EMA{cfg.nifty_ema}")
    if weak_breadth:
        reasons.append(f"breadth {breadth_pct:.0f}% < {cfg.breadth_risk_off_pct}%")

    if vix_spike or (not above and weak_breadth):
        regime = Regime.RISK_OFF
    elif above and rising and strong_breadth and vix_calm:
        regime = Regime.RISK_ON
        # Say "no VIX" rather than "VIX calm" when there is no VIX series at all
        # (crypto has no equivalent, by design) - `vix_calm` is True in that case
        # because a missing reading must not block risk_on, but claiming the index
        # is calm when it was never read is a different statement from the truth.
        vix_note = "VIX calm" if vix_last is not None else "no VIX for this market"
        reasons = [f"above rising EMA{cfg.nifty_ema}, breadth {breadth_pct:.0f}%, {vix_note}"]
    else:
        regime = Regime.NEUTRAL
        if not rising:
            reasons.append(f"EMA{cfg.nifty_ema} not rising")
        if not strong_breadth:
            reasons.append(f"breadth not > {cfg.breadth_risk_on_pct}%")
        if not vix_calm:
            reasons.append(f"VIX {vix_last:.1f} not calm")

    return RegimeSnapshot(
        on=when,
        regime=regime,
        benchmark_close=last_close,
        benchmark_ema=last_ema,
        ema_rising=rising,
        above_ema=above,
        breadth_pct=breadth_pct,
        vix=vix_last,
        vix_change_5d_pct=vix_chg,
        size_multiplier={Regime.RISK_ON: 1.0, Regime.NEUTRAL: 0.5, Regime.RISK_OFF: 0.0}[regime],
        reasons=reasons,
    )
