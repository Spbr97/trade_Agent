from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.engine.charts import frame, trend
from tradedesk.config.models import RegimeConfig, RelativeStrengthConfig
from tradedesk.engine import relative_strength as rs
from tradedesk.engine.regime import Regime, breadth_above_ema, classify_regime

CFG = RegimeConfig()


def _vix(level: float, n: int = 30) -> pd.Series:
    return pd.Series([level] * n)


def test_risk_on_when_trend_breadth_and_vix_agree() -> None:
    bench = frame(trend(120, start=20000, step_pct=0.003, seed=1))
    snap = classify_regime(bench, breadth_pct=65.0, vix=_vix(13), cfg=CFG)
    assert snap.regime is Regime.RISK_ON and snap.size_multiplier == 1.0
    assert snap.above_ema and snap.ema_rising


def test_risk_off_on_downtrend_with_weak_breadth() -> None:
    bench = frame(trend(120, start=20000, step_pct=-0.004, seed=2))
    snap = classify_regime(bench, breadth_pct=30.0, vix=_vix(15), cfg=CFG)
    assert snap.regime is Regime.RISK_OFF and snap.size_multiplier == 0.0
    assert any("below EMA" in r for r in snap.reasons)


def test_vix_spike_alone_forces_risk_off() -> None:
    bench = frame(trend(120, start=20000, step_pct=0.003, seed=1))
    spiking = pd.Series([12.0] * 25 + [12.0, 13.0, 14.0, 15.5, 16.0, 16.5])  # +37% in 5 sessions
    snap = classify_regime(bench, breadth_pct=65.0, vix=spiking, cfg=CFG)
    assert snap.regime is Regime.RISK_OFF
    assert snap.vix_change_5d_pct == pytest.approx((16.5 / 12.0 - 1) * 100)
    snap = classify_regime(bench, breadth_pct=65.0, vix=_vix(26), cfg=CFG)
    assert snap.regime is Regime.RISK_OFF


def test_neutral_when_mixed() -> None:
    bench = frame(trend(120, start=20000, step_pct=0.003, seed=1))
    snap = classify_regime(bench, breadth_pct=45.0, vix=_vix(13), cfg=CFG)  # breadth 40-50
    assert snap.regime is Regime.NEUTRAL and snap.size_multiplier == 0.5
    snap = classify_regime(bench, breadth_pct=65.0, vix=_vix(21), cfg=CFG)  # VIX not calm
    assert snap.regime is Regime.NEUTRAL
    snap = classify_regime(bench, breadth_pct=None, vix=None, cfg=CFG)  # unknown -> not risk_on
    assert snap.regime is Regime.NEUTRAL


def test_missing_vix_fails_closed_when_required() -> None:
    """The real bug (2026-09-12): a case-mismatched instrument lookup left NSE's vix_code
    unresolved, and `vix_calm = vix_last is None or ...` treated that as calm - so a
    strong trend + strong breadth still classified RISK_ON without ever checking VIX.
    With `vix_required=True` (Market.vix_required, True for NSE) the same missing-VIX
    input must now force RISK_OFF instead, per the "fail closed" hard rule."""
    bench = frame(trend(120, start=20000, step_pct=0.003, seed=1))  # strong uptrend
    without_flag = classify_regime(bench, breadth_pct=65.0, vix=None, cfg=CFG)
    assert without_flag.regime is Regime.RISK_ON  # unchanged default: not required

    failed_closed = classify_regime(bench, breadth_pct=65.0, vix=None, cfg=CFG, vix_required=True)
    assert failed_closed.regime is Regime.RISK_OFF
    assert failed_closed.size_multiplier == 0.0
    assert failed_closed.vix is None
    assert any("failing closed" in r for r in failed_closed.reasons)


def test_vix_required_is_a_noop_once_real_vix_data_is_present() -> None:
    """vix_required only changes behaviour when vix is missing - a market that requires
    VIX and HAS it classifies exactly as before."""
    bench = frame(trend(120, start=20000, step_pct=0.003, seed=1))
    a = classify_regime(bench, breadth_pct=65.0, vix=_vix(13), cfg=CFG, vix_required=False)
    b = classify_regime(bench, breadth_pct=65.0, vix=_vix(13), cfg=CFG, vix_required=True)
    assert a == b
    assert b.regime is Regime.RISK_ON


def test_crypto_market_never_requires_vix_nse_always_does() -> None:
    from tradedesk.config import load_config
    from tradedesk.markets import crypto_market, nse_market

    settings = load_config(".")
    assert nse_market(settings).vix_required is True
    assert crypto_market(settings).vix_required is False


def test_regime_uses_only_bars_up_to_evaluation_date() -> None:
    bench = frame(trend(150, start=20000, step_pct=0.003, seed=3))
    on = bench.index[99].date()
    early = classify_regime(bench.iloc[:100], breadth_pct=60.0, vix=_vix(13), cfg=CFG)
    assert early.on == on
    # The same bars plus a later crash must not change the snapshot for that date.
    crashed = bench.copy()
    crashed.loc[crashed.index[100:], ["open", "high", "low", "close"]] *= 0.6
    again = classify_regime(crashed.iloc[:100], breadth_pct=60.0, vix=_vix(13), cfg=CFG)
    assert again == early


def test_breadth_above_ema() -> None:
    up = pd.Series(trend(80, step_pct=0.01, seed=4))
    down = pd.Series(trend(80, step_pct=-0.01, seed=5))
    closes = pd.DataFrame({"A": up, "B": down, "C": up, "D": [np.nan] * 80})
    b = breadth_above_ema(closes, 20)
    assert b.iloc[-1] == pytest.approx(200 / 3)  # 2 of 3 listed stocks (D is unlisted)


# ---------------------------------------------------------------- relative strength


def test_rs_rank_orders_by_relative_return() -> None:
    cfg = RelativeStrengthConfig(lookbacks_sessions=[5, 10], weights=["0.5", "0.5"])  # type: ignore[list-item]
    n = 40
    bench = pd.Series([100 * 1.01**i for i in range(n)])
    closes = pd.DataFrame(
        {
            "STRONG": [100 * 1.03**i for i in range(n)],
            "MATCH": [50 * 1.01**i for i in range(n)],
            "WEAK": [100 * 0.99**i for i in range(n)],
        }
    )
    score = rs.rs_score(closes, bench, cfg)
    assert score["MATCH"].iloc[-1] == pytest.approx(0.0, abs=1e-12)
    assert score["STRONG"].iloc[-1] > 0 > score["WEAK"].iloc[-1]
    rank = rs.rs_rank(closes, bench, cfg)
    assert rank.iloc[-1].to_dict() == pytest.approx(
        {"STRONG": 100.0, "MATCH": 200 / 3, "WEAK": 100 / 3}
    )
    assert rank.iloc[:10].isna().all().all()  # no rank before the longest lookback


def test_rs_line_new_high_and_sector_half() -> None:
    n = 30
    bench = pd.Series([100.0] * n)
    closes = pd.DataFrame({"A": [100 + i for i in range(n)], "B": [100 - i for i in range(n)]})
    nh = rs.rs_line_new_high(closes, bench, lookback=10)
    assert bool(nh["A"].iloc[-1]) and not bool(nh["B"].iloc[-1])
    row = pd.Series({"IT": 90.0, "BANK": 60.0, "AUTO": 30.0, "METAL": np.nan})
    assert rs.stronger_half(row) == {"IT", "BANK"}
    assert rs.stronger_half(pd.Series(dtype=float)) == set()
