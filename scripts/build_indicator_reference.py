"""M4 alternative to a TradingView export (premium-gated, not available): an INDEPENDENT
reference implementation of the documented conventions - EMA span-seeded on the first
value, RMA/Wilder SMA-seeded and NaN until n finite values, ADX/DMI built on that RMA -
written fresh here, not importing engine/indicators.py. Run once against real, already-
loaded NSE history to produce a CSV that tests/golden/test_indicators_tradingview.py
(unmodified - it does not know or care where a CSV came from) checks the real
implementation against.

Same honesty rule as tests/golden/rate_card_examples.yaml: this catches an
implementation bug (wrong seed, off-by-one, wrong smoothing) in engine/indicators.py,
because a genuinely independent second implementation of the same formula agreeing with
it is real evidence - but it cannot catch both implementations sharing a misreading of
what TradingView itself does. Replace with a real TradingView export if premium access
ever becomes available.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradedesk.broker.indstocks.models import Interval
from tradedesk.data.candle_store import CandleStore

OUT = Path(__file__).resolve().parent.parent / "tests" / "golden" / "indicators"


def ref_ema(s: pd.Series, n: int) -> pd.Series:
    alpha = 2 / (n + 1)
    values = s.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=s.index)


def ref_rma(s: pd.Series, n: int) -> pd.Series:
    values = s.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    start = None
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        if np.isfinite(window).all():
            start = i
            break
    if start is None:
        return pd.Series(out, index=s.index)
    out[start] = values[start - n + 1 : start + 1].mean()
    alpha = 1 / n
    for i in range(start + 1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=s.index)


def ref_true_range(df: pd.DataFrame) -> pd.Series:
    h, low, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    out = np.empty(len(df))
    out[0] = h[0] - low[0]
    for i in range(1, len(df)):
        out[i] = max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1]))
    return pd.Series(out, index=df.index)


def ref_rsi(close: pd.Series, n: int) -> pd.Series:
    change = close.diff().fillna(0.0)
    up = ref_rma(change.clip(lower=0), n)
    down = ref_rma((-change).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + up / down.replace(0, np.nan))
    out = out.mask(down == 0, 100.0).mask((up == 0) & (down != 0), 0.0)
    return out.mask(up.isna() | down.isna(), np.nan)


def ref_atr(df: pd.DataFrame, n: int) -> pd.Series:
    return ref_rma(ref_true_range(df), n)


def ref_adx(df: pd.DataFrame, n: int) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    trur = ref_rma(ref_true_range(df), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus = 100 * ref_rma(plus_dm, n) / trur
        minus = 100 * ref_rma(minus_dm, n) / trur
        total = plus + minus
        dx = 100 * (plus - minus).abs() / total.where(total != 0, 1.0)
    return ref_rma(dx, n)


def build(code: str, out_name: str) -> None:
    store = CandleStore("data/tradedesk.duckdb")
    try:
        df = store.load(code, Interval.D1, adjusted=True)
    finally:
        store.close()
    out = df.copy()
    out["EMA20"] = ref_ema(df["close"], 20)
    out["RSI14"] = ref_rsi(df["close"], 14)
    out["ATR14"] = ref_atr(df, 14)
    out["ADX14"] = ref_adx(df, 14)
    out = out.reset_index().rename(columns={"ts": "time"})
    out["time"] = out["time"].astype("int64") // 10**9  # unix seconds, matches a TV export
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / out_name
    out.to_csv(path, index=False)
    print(f"wrote {path}: {len(out)} rows, {out['EMA20'].notna().sum()} EMA20, "
          f"{out['ADX14'].notna().sum()} ADX14")  # fmt: skip


if __name__ == "__main__":
    build("NSE_3045", "sbin_daily_reference.csv")
