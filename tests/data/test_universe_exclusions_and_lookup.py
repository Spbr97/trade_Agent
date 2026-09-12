"""Regressions for two silent-failure bugs found during the 2026-09-12 health check.

Both shared a failure mode: a lookup returned "nothing" and every layer downstream
treated that as a legitimate answer, so nothing ever raised, logged, or looked wrong.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.universe import UniverseRules, universe_on


@pytest.fixture
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    with CandleStore(tmp_path / "t.duckdb") as s:
        yield s


def _candles(code: str, n: int, close: float, volume: int) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=IST)
    return [
        Candle(
            scrip_code=code,
            interval=Interval.D1,
            ts=start + timedelta(days=i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
        )
        for i in range(n)
    ]


# ------------------------------------------------------- case-insensitive lookup


def test_index_lookup_is_case_insensitive(store: CandleStore) -> None:
    """config/universe.yaml says "INDIA VIX"; the INDstocks feed stores "India VIX".

    The lookup was case-sensitive, so it returned None, `cfg.vix_code` was None, and
    engine/regime.py - which accepts `vix: pd.Series | None` and degrades quietly - ran
    without its volatility input for the whole project without one error anywhere.
    """
    from tradedesk.broker.indstocks.models import IndexInstrument

    store.upsert_instruments(
        [IndexInstrument(exch="NSE", security_id="40000107", name="India VIX")]
    )
    assert store.index_code("India VIX") == "NSE_40000107"
    assert store.index_code("INDIA VIX") == "NSE_40000107"  # the config spelling
    assert store.index_code("india vix") == "NSE_40000107"
    assert store.index_code("NOT A REAL INDEX") is None


# ------------------------------------------------------------- universe exclusions


def test_excluded_codes_are_dropped_however_liquid(store: CandleStore) -> None:
    """Stablecoins are the MOST liquid INR pairs, so a turnover floor selects FOR them:
    CDX_USDCINR alone was 51 of the 154 signals in the first crypto research dataset."""
    store.upsert_candles(_candles("CDX_USDTINR", 25, 98.0, 1_000_000))
    store.upsert_candles(_candles("CDX_BTCINR", 25, 7_700_000.0, 100))
    codes = ["CDX_USDTINR", "CDX_BTCINR"]
    rules = UniverseRules(min_avg_turnover_inr=1.0, min_price=0.0)

    assert universe_on(store, codes, date(2026, 2, 1), rules) == ["CDX_BTCINR", "CDX_USDTINR"]

    excluded = UniverseRules(
        min_avg_turnover_inr=1.0, min_price=0.0, exclude_codes=frozenset({"CDX_USDTINR"})
    )
    assert universe_on(store, codes, date(2026, 2, 1), excluded) == ["CDX_BTCINR"]


def test_nse_universe_rules_exclude_nothing_by_default() -> None:
    assert UniverseRules().exclude_codes == frozenset()


def test_crypto_market_excludes_stablecoins_and_nse_does_not() -> None:
    from tradedesk.config import load_config
    from tradedesk.markets import crypto_market, nse_market

    settings = load_config(".")
    assert crypto_market(settings).universe_rules.exclude_codes >= {
        "CDX_USDTINR",
        "CDX_USDCINR",
    }
    assert nse_market(settings).universe_rules.exclude_codes == frozenset()
