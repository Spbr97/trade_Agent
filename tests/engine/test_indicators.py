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


# ------------------------------------------------- intraday: session VWAP and bands


def _intraday_frame(days: int = 3, bars: int = 8) -> pd.DataFrame:
    """`days` IST sessions of `bars` 15-minute bars from 09:15, with varying volume so a
    volume-WEIGHTED mean is distinguishable from a plain one."""
    rows, idx = [], []
    for d in range(days):
        day = pd.Timestamp("2026-09-07", tz="Asia/Kolkata") + pd.Timedelta(days=d)
        for b in range(bars):
            base = 100.0 + d * 10 + b
            rows.append(
                {
                    "open": base,
                    "high": base + 1.0,
                    "low": base - 1.0,
                    "close": base + 0.5,
                    "volume": 1000 * (b + 1),
                }
            )
            idx.append(day + pd.Timedelta(minutes=15 * b) + pd.Timedelta(hours=9, minutes=15))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def test_session_vwap_matches_a_hand_computed_reference() -> None:
    """VWAP anchors the whole intraday setup library, so it is checked against an explicit
    cumulative sum(tp*v)/sum(v) rather than only against itself."""
    df = _intraday_frame()
    got = ind.session_vwap(df)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sess = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata").normalize()
    for day in sess.unique():
        m = sess == day
        cum_pv = (tp[m] * df["volume"][m]).cumsum()
        cum_v = df["volume"][m].cumsum()
        expected = cum_pv / cum_v
        for a, b in zip(got[m].to_numpy(), expected.to_numpy(), strict=True):
            assert a == pytest.approx(b)


def test_session_vwap_resets_every_session() -> None:
    """The bug this guards: accumulating from one anchor forever turns VWAP into a
    multi-session average nobody trades off. The first bar of each day must equal that
    bar's own typical price."""
    df = _intraday_frame()
    v = ind.session_vwap(df)
    sess = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata").normalize()
    tp = (df["high"] + df["low"] + df["close"]) / 3
    first = ~pd.Series(sess, index=df.index).duplicated()
    assert first.sum() == 3
    for ts in df.index[first.to_numpy()]:
        assert v.loc[ts] == pytest.approx(tp.loc[ts])


def test_vwap_bands_straddle_vwap_and_widen_with_dispersion() -> None:
    df = _intraday_frame()
    vwap, up, lo = ind.vwap_bands(df, 1.0)
    ok = vwap.notna()
    assert (up[ok] >= vwap[ok]).all() and (lo[ok] <= vwap[ok]).all()
    _, up2, lo2 = ind.vwap_bands(df, 2.0)
    # 2 sigma must be at least as wide as 1 sigma everywhere it is defined
    w1, w2 = (up - lo)[ok], (up2 - lo2)[ok]
    assert (w2 >= w1 - 1e-9).all()
    # first bar of a session has zero dispersion so far, so the band is degenerate
    assert (up.iloc[0] - lo.iloc[0]) == pytest.approx(0.0)


def test_intraday_features_have_no_look_ahead() -> None:
    """Same property the daily frame is held to, and it matters more here: the session
    columns accumulate, so an off-by-one in the session boundary would leak the day's
    later bars into an earlier one."""
    df = _intraday_frame(days=4, bars=10)
    full = ind.intraday_features(df)
    for k in (5, 11, 25, 33):
        partial = ind.intraday_features(df.iloc[: k + 1])
        a, b = full.iloc[k], partial.iloc[-1]
        for col in full.columns:
            va, vb = a[col], b[col]
            if isinstance(va, (float, np.floating)) and math.isnan(va):
                assert math.isnan(vb), col
            else:
                assert va == pytest.approx(vb), f"{col} at bar {k} differs: {va} vs {vb}"


def test_intraday_features_session_columns_reset_daily() -> None:
    f = ind.intraday_features(_intraday_frame(days=3, bars=8))
    assert (f["session_bar"].to_numpy() == list(range(8)) * 3).all()
    # session_high/low are running extremes WITHIN the day, never across it
    day2 = f.iloc[8:16]
    assert day2["session_high"].iloc[0] == pytest.approx(day2["high"].iloc[0])
    assert day2["session_high"].iloc[-1] == pytest.approx(day2["high"].max())


def test_intraday_features_omit_daily_only_columns() -> None:
    """Not a superset of daily_features on purpose - a 200 EMA or 52-week range computed
    off 1-minute bars is a number with no meaning, and having the column present would
    invite exactly that mistake."""
    f = ind.intraday_features(_intraday_frame())
    for col in ("ema200", "high52w", "low52w", "prev_week_high"):
        assert col not in f.columns
    for col in ("vwap", "vwap_upper1", "vwap_lower1", "dist_vwap_atr", "session_high"):
        assert col in f.columns
