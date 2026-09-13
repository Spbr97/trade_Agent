"""Indicator library (PLAN.md 6.3), pure pandas/numpy, TradingView conventions.

Conventions matter for the golden tests against TradingView exports:
- ema: alpha = 2/(n+1), seeded with the first value            (ta.ema)
- rma: alpha = 1/n, seeded with the SMA of the first n values  (ta.rma) - used by RSI, ATR, DMI
- stdev: population (biased), as ta.stdev's default              (Bollinger width)
- tr: max(h-l, |h-c1|, |l-c1|), first bar h-l                    (ta.tr)
Every function is causal: the value at bar i uses bars <= i only (look-ahead tests enforce it).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ smoothing


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=1).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average: SMA seed at bar n-1, then alpha = 1/n recursion."""
    values = s.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    finite = np.isfinite(values)
    # TradingView seeds with ta.sma, which is na until n consecutive finite values exist.
    first = -1
    for i in range(n - 1, len(values)):
        if finite[i - n + 1 : i + 1].all():
            first = i
            break
    if first < 0:
        return pd.Series(out, index=s.index)
    seed = float(values[first - n + 1 : first + 1].mean())
    out[first] = seed
    alpha = 1.0 / n
    prev = seed
    for i in range(first + 1, len(values)):
        v = values[i]
        prev = prev if np.isnan(v) else alpha * v + (1 - alpha) * prev
        out[i] = prev
    return pd.Series(out, index=s.index)


def stdev(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).std(ddof=0)


# ------------------------------------------------------------------- momentum


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """ta.rsi: down == 0 -> 100, up == 0 -> 0, else 100 - 100 / (1 + up/down)."""
    change = close.diff().fillna(0.0)
    up = rma(change.clip(lower=0), n)
    down = rma((-change).clip(lower=0), n)
    out = 100 - 100 / (1 + up / down.replace(0, np.nan))
    out = out.mask(down == 0, 100.0).mask((up == 0) & (down != 0), 0.0)
    return out.mask(up.isna() | down.isna(), np.nan)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def roc(close: pd.Series, n: int) -> pd.Series:
    """Rate of change in percent: (close / close[n] - 1) * 100."""
    return (close / close.shift(n) - 1) * 100


# ----------------------------------------------------------------- volatility


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    hl = df["high"] - df["low"]
    hc = (df["high"] - prev_close).abs()
    lc = (df["low"] - prev_close).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    tr.iloc[0] = hl.iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return atr(df, n) / df["close"] * 100


def bollinger_width(close: pd.Series, n: int = 20, mult: float = 2.0) -> pd.Series:
    basis = sma(close, n)
    dev = mult * stdev(close, n)
    return (2 * dev) / basis


def adx(df: pd.DataFrame, n: int = 14, smoothing: int | None = None) -> pd.DataFrame:
    """TradingView ta.dmi: columns plus_di, minus_di, adx."""
    smoothing = smoothing or n
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    trur = rma(true_range(df), n)
    plus = 100 * rma(plus_dm, n) / trur
    minus = 100 * rma(minus_dm, n) / trur
    total = plus + minus
    dx = 100 * (plus - minus).abs() / total.where(total != 0, 1.0)
    return pd.DataFrame({"plus_di": plus, "minus_di": minus, "adx": rma(dx, smoothing)})


# --------------------------------------------------------------------- volume


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff().fillna(0))
    out: pd.Series = (sign * df["volume"]).cumsum()
    return out


def volume_ratio(df: pd.DataFrame, n: int = 50) -> pd.Series:
    """Today's volume relative to its n-day average (excluding today)."""
    return df["volume"] / sma(df["volume"].shift(1), n)


def volume_dryup(df: pd.DataFrame, short: int = 5, long: int = 50) -> pd.Series:
    """Recent average volume vs longer average; < 1 means volume is drying up."""
    return sma(df["volume"], short) / sma(df["volume"], long)


def updown_volume_ratio(df: pd.DataFrame, n: int = 20) -> pd.Series:
    up_days = df["close"] > df["close"].shift(1)
    up_vol = df["volume"].where(up_days, 0).rolling(n, min_periods=n).sum()
    down_vol = df["volume"].where(~up_days, 0).rolling(n, min_periods=n).sum()
    return up_vol / down_vol.replace(0, np.nan)


# -------------------------------------------------------------- range / bars


def bar_range(df: pd.DataFrame) -> pd.Series:
    return df["high"] - df["low"]


def nr7(df: pd.DataFrame, n: int = 7) -> pd.Series:
    r = bar_range(df)
    return (r <= r.rolling(n, min_periods=n).min()) & r.rolling(n, min_periods=n).min().notna()


def inside_day(df: pd.DataFrame) -> pd.Series:
    return (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))


def range_contraction(df: pd.DataFrame, short: int = 5, long: int = 20) -> pd.Series:
    """Mean range of the last `short` bars over the mean of the last `long`; < 1 = tightening."""
    r = bar_range(df)
    return sma(r, short) / sma(r, long)


def anchored_vwap(df: pd.DataFrame, anchor: int) -> pd.Series:
    """VWAP accumulated from positional index `anchor` onward; NaN before it."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    if 0 <= anchor < len(df):
        cum_pv = np.cumsum(pv[anchor:])
        cum_v = np.cumsum(v[anchor:])
        with np.errstate(divide="ignore", invalid="ignore"):
            out[anchor:] = np.where(cum_v > 0, cum_pv / cum_v, np.nan)
    return pd.Series(out, index=df.index)


def _session_ids(index: pd.Index) -> np.ndarray:
    """Integer id per IST trading date, so every intraday accumulation restarts at the open.

    Uses the exchange date of each bar's timestamp, never the local clock (CLAUDE.md: candle
    logic uses exchange timestamps). A tz-naive index is assumed to already be IST rather
    than silently localised, which would shift every bar near the session boundary."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("Asia/Kolkata")
    dates = idx.normalize()
    _, ids = np.unique(dates.to_numpy(), return_inverse=True)
    return np.asarray(ids, dtype=int)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price accumulated from each session's OPEN, reset daily.

    This is the intraday reference the SDD's VWAP Reclaim setup is built on, and it is a
    different object from `anchored_vwap`: that one accumulates from a single positional
    anchor forever, which across multiple days drifts into a multi-session average nobody
    trades off. Causal by construction - bar i only ever sees bars <= i within its own
    session."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    sess = _session_ids(df.index)
    out = np.full(len(df), np.nan)
    start = 0
    for i in range(1, len(sess) + 1):
        if i == len(sess) or sess[i] != sess[start]:
            cum_pv = np.cumsum(pv[start:i])
            cum_v = np.cumsum(v[start:i])
            with np.errstate(divide="ignore", invalid="ignore"):
                out[start:i] = np.where(cum_v > 0, cum_pv / cum_v, np.nan)
            start = i
    return pd.Series(out, index=df.index)


def vwap_bands(df: pd.DataFrame, k: float = 1.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(vwap, upper, lower) where the band is k volume-weighted standard deviations of the
    typical price about the session VWAP, accumulated from the session open.

    Volume-weighted, not a plain stdev of price: a band that ignores volume drifts away from
    where the session actually traded, which is the whole point of using VWAP as the
    reference. Population (biased) form, matching this module's `stdev` convention."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tpv = tp.to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    sess = _session_ids(df.index)
    vwap = session_vwap(df).to_numpy(dtype=float)
    dev = np.full(len(df), np.nan)
    start = 0
    for i in range(1, len(sess) + 1):
        if i == len(sess) or sess[i] != sess[start]:
            cum_v = np.cumsum(v[start:i])
            # E[(tp - vwap)^2] weighted by volume, accumulated within the session
            sq = np.cumsum(v[start:i] * (tpv[start:i] - vwap[start:i]) ** 2)
            with np.errstate(divide="ignore", invalid="ignore"):
                dev[start:i] = np.where(cum_v > 0, np.sqrt(sq / cum_v), np.nan)
            start = i
    w = pd.Series(vwap, index=df.index)
    d = pd.Series(dev, index=df.index)
    return w, w + k * d, w - k * d


def rolling_high(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).max()


def rolling_low(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).min()


# ------------------------------------------------------------ feature frame


def daily_features(df: pd.DataFrame) -> pd.DataFrame:
    """All daily indicators the setups and scorer use, as extra columns on a copy of df."""
    out = df.copy()
    c = out["close"]
    for n in (10, 20, 50, 200):
        out[f"ema{n}"] = ema(c, n)
        out[f"ema{n}_slope"] = out[f"ema{n}"].pct_change(5)  # 5-bar slope, fraction
    d = adx(out, 14)
    out["plus_di"], out["minus_di"], out["adx14"] = d["plus_di"], d["minus_di"], d["adx"]
    out["rsi14"] = rsi(c, 14)
    out["rsi2"] = rsi(c, 2)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c)
    out["roc5"] = roc(c, 5)
    out["roc20"] = roc(c, 20)
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = out["atr14"] / c * 100
    # Where this stock's current volatility sits against its OWN trailing year, so a 3%
    # ATR reads differently for a habitually quiet name than for a jumpy one. Trailing
    # window only (rolling includes the current bar and nothing after it).
    out["atr_pct_rank"] = out["atr_pct"].rolling(250, min_periods=60).rank(pct=True) * 100
    out["bb_width"] = bollinger_width(c, 20)
    out["nr7"] = nr7(out)
    out["inside_day"] = inside_day(out)
    out["range_contraction"] = range_contraction(out)
    out["vol_ratio50"] = volume_ratio(out, 50)
    out["vol_dryup"] = volume_dryup(out)
    out["updown_vol20"] = updown_volume_ratio(out, 20)
    out["obv"] = obv(out)
    out["high52w"] = rolling_high(out["high"], 250)
    out["low52w"] = rolling_low(out["low"], 250)
    out["high20"] = rolling_high(out["high"], 20)
    out["prev_week_high"] = out["high"].shift(1).rolling(5, min_periods=1).max()
    out["prev_week_low"] = out["low"].shift(1).rolling(5, min_periods=1).min()
    return out


def intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators for a single intraday timeframe (SDD sections 4-7), as extra columns.

    Deliberately NOT a superset of daily_features. Anything anchored to a session - VWAP and
    its bands, the day's running high/low, the opening range - is meaningless on daily bars,
    and anything needing a year of history (52-week position, 200 EMA) is meaningless on a
    frame of 1-minute bars. Keeping them apart stops a caller from reading a column that is
    technically present but nonsense at its timeframe.

    `df` must be ONE interval's bars, ascending, with an IST (or IST-naive) index. Every
    column is causal: bar i uses bars <= i only, and session-anchored columns additionally
    reset at each session open - the leakage test covers both."""
    out = df.copy()
    c = out["close"]
    for n in (9, 20, 50):
        out[f"ema{n}"] = ema(c, n)
    d = adx(out, 14)
    out["plus_di"], out["minus_di"], out["adx14"] = d["plus_di"], d["minus_di"], d["adx"]
    out["rsi14"] = rsi(c, 14)
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = out["atr14"] / c * 100
    out["bb_width"] = bollinger_width(c, 20)
    out["range_contraction"] = range_contraction(out)
    out["vol_ratio20"] = volume_ratio(out, 20)

    vwap, upper, lower = vwap_bands(out, 1.0)
    out["vwap"] = vwap
    out["vwap_upper1"] = upper
    out["vwap_lower1"] = lower
    _, upper2, lower2 = vwap_bands(out, 2.0)
    out["vwap_upper2"] = upper2
    out["vwap_lower2"] = lower2
    # Distance to VWAP in ATR units: comparable across instruments, unlike a raw rupee gap.
    with np.errstate(divide="ignore", invalid="ignore"):
        out["dist_vwap_atr"] = (c - vwap) / out["atr14"].replace(0, np.nan)
    out["above_vwap"] = c > vwap

    # Session-anchored running extremes, reset each day (SDD section 4 market structure).
    sess = pd.Series(_session_ids(out.index), index=out.index)
    g = out.groupby(sess, sort=False)
    out["session_high"] = g["high"].cummax()
    out["session_low"] = g["low"].cummin()
    out["session_bar"] = g.cumcount()
    return out
