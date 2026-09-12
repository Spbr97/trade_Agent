"""Features for the meta-labeling model (PLAN.md 10.3). Everything is known at the arming
close: the last row of the daily feature frame sliced to that date, the signal's own
geometry, and the snapshot context (regime, breadth, VIX, results proximity).

Hard rule: no feature may use information from after the moment it is recorded. The
leakage test truncates history after the arming date and asserts identical features.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from tradedesk.engine.scoring import room_in_r
from tradedesk.engine.signals import SetupKind, Signal

FEATURE_VERSION = "v3"  # bump whenever FEATURE_NAMES changes, so a saved model's artifact
# records exactly which feature set it was trained against (v1 was the original 31; v2
# added nifty_return_1d/5d on 2026-09-12; v3 on 2026-09-13 replaced sector_percentile - a
# dead stub that always defaulted to 50.0, since nothing anywhere ever populated it - with
# real sector_return_1d/5d, computed from config/sector_membership.yaml + loaded sector
# index candles; predict.py refuses to score a bundle whose feature_version doesn't match,
# rather than silently misaligning columns)

FEATURE_NAMES: list[str] = [
    "dist_ema20_atr",
    "dist_ema50_atr",
    "dist_ema200_atr",
    "ema20_slope",
    "ema50_slope",
    "adx14",
    "rsi14",
    "rs_percentile",
    "sector_return_1d",
    "sector_return_5d",
    "atr_pct",
    "stop_atr",
    "room_r",
    "base_depth",
    "base_contraction",
    "base_dryup",
    "base_tests",
    "vol_ratio50",
    "updown_vol20",
    "nr7",
    "inside_day",
    "breadth_pct",
    "vix",
    "vix_change_5d",
    "regime_risk_on",
    "regime_risk_off",
    "sessions_to_results",
    "day_of_week",
    "expiry_week",
    "nifty_return_1d",
    "nifty_return_5d",
    "setup_base_breakout",
    "setup_trend_pullback",
    "setup_nr7_breakout",
]


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else v


def signal_features(
    sig: Signal,
    feats: pd.DataFrame,
    *,
    sector_return_1d: float | None = None,
    sector_return_5d: float | None = None,
    breadth_pct: float | None = None,
    vix: float | None = None,
    vix_change_5d: float | None = None,
    sessions_to_results: int | None = None,
    expiry_week: bool = False,
    nifty_return_1d: float | None = None,
    nifty_return_5d: float | None = None,
) -> dict[str, float]:
    """`feats` must be the daily feature frame sliced to the arming session (last row = the
    arming close). Returns a flat dict keyed by FEATURE_NAMES."""
    last = feats.iloc[-1]
    atr = _f(last.get("atr14"), 0.0) or 1e-9
    close = _f(last.get("close"))
    g = sig.geometry
    base = g.get("base") or {}
    room = room_in_r(sig)
    armed = pd.Timestamp(feats.index[-1])
    out = {
        "dist_ema20_atr": (close - _f(last.get("ema20"))) / atr,
        "dist_ema50_atr": (close - _f(last.get("ema50"))) / atr,
        "dist_ema200_atr": (close - _f(last.get("ema200"))) / atr,
        "ema20_slope": _f(last.get("ema20_slope")) * 100,
        "ema50_slope": _f(last.get("ema50_slope")) * 100,
        "adx14": _f(last.get("adx14")),
        "rsi14": _f(last.get("rsi14"), 50.0),
        "rs_percentile": _f(sig.rs_percentile, 50.0),
        "sector_return_1d": _f(sector_return_1d),
        "sector_return_5d": _f(sector_return_5d),
        "atr_pct": _f(last.get("atr_pct")),
        "stop_atr": sig.risk_per_share / atr,
        "room_r": 5.0 if room is None else min(float(room), 5.0),
        "base_depth": _f(base.get("depth_pct")),
        "base_contraction": _f(base.get("contraction_ratio"), 1.0),
        "base_dryup": _f(base.get("volume_dryup"), 1.0),
        "base_tests": _f(base.get("tests_of_high")),
        "vol_ratio50": _f(last.get("vol_ratio50"), 1.0),
        "updown_vol20": _f(last.get("updown_vol20"), 1.0),
        "nr7": 1.0 if bool(last.get("nr7", False)) else 0.0,
        "inside_day": 1.0 if bool(last.get("inside_day", False)) else 0.0,
        "breadth_pct": _f(breadth_pct, 50.0),
        "vix": _f(vix, 15.0),
        "vix_change_5d": _f(vix_change_5d),
        "regime_risk_on": 1.0 if sig.regime == "risk_on" else 0.0,
        "regime_risk_off": 1.0 if sig.regime == "risk_off" else 0.0,
        "sessions_to_results": 30.0
        if sessions_to_results is None
        else float(min(sessions_to_results, 30)),
        "day_of_week": float(armed.weekday()),
        "expiry_week": 1.0 if expiry_week else 0.0,
        "nifty_return_1d": _f(nifty_return_1d),
        "nifty_return_5d": _f(nifty_return_5d),
        "setup_base_breakout": 1.0 if sig.setup is SetupKind.BASE_BREAKOUT else 0.0,
        "setup_trend_pullback": 1.0 if sig.setup is SetupKind.TREND_PULLBACK else 0.0,
        "setup_nr7_breakout": 1.0 if sig.setup is SetupKind.NR7_BREAKOUT else 0.0,
    }
    assert set(out) == set(FEATURE_NAMES)
    return out


def to_frame(rows: list[Mapping[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=FEATURE_NAMES).astype(float)
