"""Indicator tests: independent loop implementations (Wilder / TradingView definitions)
versus the vectorised library, hand cases, and the look-ahead (no-leak) property."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tests.engine.charts import frame, trend
from tradedesk.engine import indicators as ind


def _wilder_rma(values: list[float], n: int) -> list[float]:
    out = [math.nan] * len(values)
    if len(values) < n:
        return out
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def _loop_rsi(closes: list[float], n: int) -> list[float]:
    ups, downs = [0.0], [0.0]
    for a, b in zip(closes[:-1], closes[1:], strict=True):
        d = b - a
        ups.append(max(d, 0.0))
        downs.append(max(-d, 0.0))
    up, down = _wilder_rma(ups, n), _wilder_rma(downs, n)
    out = []
    for u, d in zip(up, down, strict=True):
        if math.isnan(u):
            out.append(math.nan)
        elif d == 0:
            out.append(100.0)
        elif u == 0:
            out.append(0.0)
        else:
            out.append(100 - 100 / (1 + u / d))
    return out


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return frame(trend(120, step_pct=0.002, seed=7))


def test_ema_recursion(df: pd.DataFrame) -> None:
    c = df["close"].to_numpy()
    e = ind.ema(df["close"], 10).to_numpy()
    alpha = 2 / 11
    assert e[0] == c[0]
    for i in range(1, len(c)):
        assert e[i] == pytest.approx(alpha * c[i] + (1 - alpha) * e[i - 1])


def test_rma_matches_wilder_loop(df: pd.DataFrame) -> None:
    got = ind.rma(df["close"], 14).to_numpy()
    want = _wilder_rma(df["close"].tolist(), 14)
    assert np.isnan(got[:13]).all()
    np.testing.assert_allclose(got[13:], want[13:], rtol=1e-12)


def test_rsi_matches_loop_and_edges(df: pd.DataFrame) -> None:
    got = ind.rsi(df["close"], 14).to_numpy()
    want = _loop_rsi(df["close"].tolist(), 14)
    np.testing.assert_allclose(got[13:], want[13:], rtol=1e-12)
    up = frame([100 + i for i in range(30)])
    assert ind.rsi(up["close"], 14).iloc[-1] == 100.0
    down = frame([100 - i for i in range(30)])
    assert ind.rsi(down["close"], 14).iloc[-1] == 0.0


def test_true_range_and_atr(df: pd.DataFrame) -> None:
    tr = ind.true_range(df)
    h, lo, c = df["high"], df["low"], df["close"]
    assert tr.iloc[0] == h.iloc[0] - lo.iloc[0]
    for i in (1, 5, 50):
        want = max(
            h.iloc[i] - lo.iloc[i], abs(h.iloc[i] - c.iloc[i - 1]), abs(lo.iloc[i] - c.iloc[i - 1])
        )
        assert tr.iloc[i] == pytest.approx(want)
    np.testing.assert_allclose(
        ind.atr(df, 14).to_numpy()[13:], _wilder_rma(tr.tolist(), 14)[13:], rtol=1e-12
    )


def test_adx_matches_loop(df: pd.DataFrame) -> None:
    n = 14
    h, lo = df["high"].tolist(), df["low"].tolist()
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, len(h)):
        up, down = h[i] - h[i - 1], lo[i - 1] - lo[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    tr = ind.true_range(df).tolist()
    trur, pdm, mdm = _wilder_rma(tr, n), _wilder_rma(plus_dm, n), _wilder_rma(minus_dm, n)
    dx = []
    for t, p, m in zip(trur, pdm, mdm, strict=True):
        if math.isnan(t):
            dx.append(math.nan)
            continue
        plus, minus = 100 * p / t, 100 * m / t
        s = plus + minus
        dx.append(100 * abs(plus - minus) / (s if s != 0 else 1))
    want_adx = _wilder_rma(dx[n - 1 :], n)
    got = ind.adx(df, n)
    np.testing.assert_allclose(got["adx"].to_numpy()[2 * n - 2 :], want_adx[n - 1 :], rtol=1e-10)
    assert (got["plus_di"].dropna() >= 0).all() and (got["minus_di"].dropna() >= 0).all()


def test_macd_roc_bb(df: pd.DataFrame) -> None:
    line, sig, hist = ind.macd(df["close"])
    c = df["close"]
    assert line.iloc[-1] == pytest.approx(ind.ema(c, 12).iloc[-1] - ind.ema(c, 26).iloc[-1])
    assert hist.iloc[-1] == pytest.approx(line.iloc[-1] - sig.iloc[-1])
    assert ind.roc(c, 5).iloc[-1] == pytest.approx((c.iloc[-1] / c.iloc[-6] - 1) * 100)
    w = ind.bollinger_width(c, 20).iloc[-1]
    window = c.iloc[-20:]
    assert w == pytest.approx(4 * window.std(ddof=0) / window.mean())


def test_nr7_inside_day_range_contraction() -> None:
    ranges = [4, 4, 4, 4, 4, 4, 1, 4, 4]  # bar 6 is the narrowest of its 7
    df = frame([100] * 9, ranges=ranges)
    n = ind.nr7(df)
    assert bool(n.iloc[6]) and not bool(n.iloc[7]) and not bool(n.iloc[5])
    df2 = frame([100, 100, 100], ranges=[4, 2, 4])
    inside = ind.inside_day(df2)
    assert bool(inside.iloc[1]) and not bool(inside.iloc[2])


def test_obv_and_volume_ratios() -> None:
    df = frame([10, 11, 10, 12], volumes=[100, 200, 300, 400])
    assert ind.obv(df).tolist() == [0, 200, -100, 300]
    df = frame([1] * 60, volumes=[100] * 59 + [250])
    assert ind.volume_ratio(df, 50).iloc[-1] == pytest.approx(2.5)
    df = frame([1, 2] * 12 + [1], volumes=[100, 300] * 12 + [100])  # up days carry 300
    r = ind.updown_volume_ratio(df, 20).iloc[-1]
    assert r > 1


def test_anchored_vwap() -> None:
    df = frame([10, 12, 14], ranges=[0, 0, 0], volumes=[1, 1, 2])
    v = ind.anchored_vwap(df, 1)
    assert math.isnan(v.iloc[0])
    tp1, tp2 = (df["high"] + df["low"] + df["close"]).iloc[1:3] / 3
    assert v.iloc[1] == pytest.approx(tp1)
    assert v.iloc[2] == pytest.approx((tp1 * 1 + tp2 * 2) / 3)


def test_daily_features_have_no_look_ahead(df: pd.DataFrame) -> None:
    """Every feature at bar k must be identical whether or not later bars exist."""
    full = ind.daily_features(df)
    for k in (30, 60, 90, 119):
        partial = ind.daily_features(df.iloc[: k + 1])
        a, b = full.iloc[k], partial.iloc[-1]
        for col in full.columns:
            va, vb = a[col], b[col]
            if isinstance(va, (float, np.floating)) and math.isnan(va):
                assert math.isnan(vb), col
            else:
                assert va == pytest.approx(vb), f"{col} at bar {k} differs: {va} vs {vb}"


def test_daily_features_columns(df: pd.DataFrame) -> None:
    f = ind.daily_features(df)
    for col in (
        "ema10", "ema20", "ema50", "ema200", "adx14", "rsi14", "rsi2", "macd", "atr14",
        "atr_pct", "bb_width", "nr7", "inside_day", "range_contraction", "vol_ratio50",
        "vol_dryup", "updown_vol20", "obv", "high52w", "low52w", "high20",
        "prev_week_high", "prev_week_low",
    ):  # fmt: skip
        assert col in f.columns, col
    assert len(f) == len(df)
