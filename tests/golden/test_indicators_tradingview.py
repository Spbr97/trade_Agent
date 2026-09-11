"""Golden tests: indicators must match TradingView exports (see indicators/README.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from tradedesk.engine import indicators as ind

FOLDER = Path(__file__).with_name("indicators")
MANIFEST = FOLDER / "manifest.yaml"


def _load_manifest() -> list[dict[str, Any]]:
    if not MANIFEST.exists():
        return []
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or []
    return [d for d in data if isinstance(d, dict)]


CASES = _load_manifest()


def _read_tv_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    lower = {c.lower(): c for c in df.columns}
    rename = {}
    for want in ("open", "high", "low", "close", "volume", "time"):
        if want in lower:
            rename[lower[want]] = want
    df = df.rename(columns=rename)
    if "time" in df.columns:
        t = df["time"]
        df.index = (
            pd.to_datetime(t, unit="s") if np.issubdtype(t.dtype, np.number) else pd.to_datetime(t)
        )
    if "volume" not in df.columns:
        df["volume"] = 0
    return df


def _compute(df: pd.DataFrame, spec: str) -> pd.Series:
    name, *args = spec.split(":")
    a = [int(x) if x.isdigit() else float(x) for x in args]
    c = df["close"]
    match name:
        case "ema":
            return ind.ema(c, int(a[0]))
        case "sma":
            return ind.sma(c, int(a[0]))
        case "rsi":
            return ind.rsi(c, int(a[0]))
        case "atr":
            return ind.atr(df, int(a[0]))
        case "adx":
            return ind.adx(df, int(a[0]))["adx"]
        case "plus_di":
            return ind.adx(df, int(a[0]))["plus_di"]
        case "minus_di":
            return ind.adx(df, int(a[0]))["minus_di"]
        case "macd_line":
            return ind.macd(c, int(a[0]), int(a[1]), int(a[2]))[0]
        case "macd_signal":
            return ind.macd(c, int(a[0]), int(a[1]), int(a[2]))[1]
        case "bb_width":
            return ind.bollinger_width(c, int(a[0]), float(a[1]) if len(a) > 1 else 2.0)
        case "roc":
            return ind.roc(c, int(a[0]))
        case "obv":
            return ind.obv(df)
    raise ValueError(f"unknown indicator spec {spec!r}")


@pytest.mark.golden
@pytest.mark.skipif(not CASES, reason="no tests/golden/indicators/manifest.yaml yet")
@pytest.mark.parametrize("case", CASES, ids=[c.get("file", "?") for c in CASES])
def test_matches_tradingview(case: dict[str, Any]) -> None:
    df = _read_tv_csv(FOLDER / case["file"])
    warmup = int(case.get("warmup", 300))
    rtol = float(case.get("rtol", 1e-3))
    problems = []
    for column, spec in case["columns"].items():
        want = pd.to_numeric(df[column], errors="coerce").to_numpy()[warmup:]
        got = _compute(df, spec).to_numpy()[warmup:]
        mask = np.isfinite(want) & np.isfinite(got)
        if mask.sum() == 0:
            problems.append(f"{column} ({spec}): nothing to compare after warmup")
            continue
        rel = np.abs(got[mask] - want[mask]) / np.maximum(np.abs(want[mask]), 1e-9)
        if rel.max() > rtol:
            i = int(np.argmax(rel))
            problems.append(f"{column} ({spec}): max rel err {rel.max():.2e} at bar {warmup + i}")
    assert not problems, "\n".join(problems)
