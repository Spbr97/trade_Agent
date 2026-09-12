from __future__ import annotations

from datetime import date, datetime, time
from fractions import Fraction
from pathlib import Path

import pytest

from tests.data.synth import daily, sessions
from tradedesk.broker.indstocks.models import IST, IndexInstrument, Instrument, Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.models import ActionKind, CorporateAction, ResultsEvent

SBIN = Instrument(
    exch="NSE", segment="E", security_id="3045", instrument_name="EQUITY",
    trading_symbol="SBIN", symbol_name="SBIN", series="EQ", tick_size=0.05, lot_units=1,
)  # fmt: skip
NIFTY = IndexInstrument(exch="NSE", name="NIFTY 50", security_id="40000001")


@pytest.fixture
def store() -> CandleStore:
    """Default store: matches the INDstocks feed, which already serves adjusted history,
    so reads never re-apply corporate-action factors."""
    with CandleStore() as s:
        s.upsert_instruments([SBIN, NIFTY])
        yield s


@pytest.fixture
def adjusting_store() -> CandleStore:
    """A store for a hypothetical feed that serves genuinely UNADJUSTED prices - the only
    configuration in which `load(adjusted=True)` rescales anything."""
    with CandleStore(apply_corporate_actions=True) as s:
        s.upsert_instruments([SBIN, NIFTY])
        yield s


def test_upsert_is_idempotent_and_replaces(store: CandleStore) -> None:
    days = sessions(date(2026, 1, 5), 5)
    c = daily("NSE_3045", days)
    assert store.upsert_candles(c) == 5
    assert store.upsert_candles(c) == 5  # replace, not duplicate
    assert store.count("NSE_3045", Interval.D1) == 5
    revised = c[-1].model_copy(update={"close": 999.0})
    store.upsert_candles([revised])
    df = store.load("NSE_3045", Interval.D1, adjusted=False)
    assert df["close"].iloc[-1] == 999.0 and len(df) == 5


def test_load_window_and_index_timezone(store: CandleStore) -> None:
    days = sessions(date(2026, 1, 5), 10)
    store.upsert_candles(daily("NSE_3045", days))
    start = datetime.combine(days[2], time.min, tzinfo=IST)
    end = datetime.combine(days[5], time.min, tzinfo=IST)  # exclusive
    df = store.load("NSE_3045", Interval.D1, start, end, adjusted=False)
    assert len(df) == 3
    assert str(df.index.tz) == "Asia/Kolkata"
    assert df.index[0].time() == time(9, 15)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert store.first_ts("NSE_3045", Interval.D1) is not None
    assert store.last_ts("NSE_3045", Interval.D1).date() == days[-1]
    assert store.last_ts("NSE_X", Interval.D1) is None
    assert store.codes(Interval.D1) == ["NSE_3045"]


def test_instrument_mapping(store: CandleStore) -> None:
    assert store.symbol_for("NSE_3045") == "SBIN"
    assert store.scrip_code_for("SBIN") == "NSE_3045"
    assert store.index_code("NIFTY 50") == "NSE_40000001"
    assert store.instrument_codes() == ["NSE_3045"]
    assert store.instrument_codes(kind="index", series=None) == ["NSE_40000001"]


def test_bonus_adjustment_applied_on_read_only(adjusting_store: CandleStore) -> None:
    days = sessions(date(2026, 1, 5), 6)
    ex = days[3]
    candles = daily("NSE_3045", days, start_price=200.0, volume=1000)
    adjusting_store.upsert_candles(candles)
    adjusting_store.upsert_corporate_actions(
        [
            CorporateAction(
                symbol="SBIN", ex_date=ex, kind=ActionKind.BONUS, price_factor=Fraction(1, 2)
            )
        ]
    )
    raw = adjusting_store.load("NSE_3045", Interval.D1, adjusted=False)
    adj = adjusting_store.load("NSE_3045", Interval.D1, adjusted=True)
    assert raw["close"].iloc[0] == 200.0  # stored data untouched
    assert adj["close"].iloc[0] == 100.0  # pre-ex bars halved ...
    assert adj["volume"].iloc[0] == 2000  # ... and volume doubled
    assert adj["close"].iloc[3] == raw["close"].iloc[3]  # ex-date bar and after unchanged
    assert adj["volume"].dtype == "int64"


def test_split_with_unparsed_ratio_is_not_applied(adjusting_store: CandleStore) -> None:
    days = sessions(date(2026, 1, 5), 4)
    adjusting_store.upsert_candles(daily("NSE_3045", days))
    adjusting_store.upsert_corporate_actions(
        [CorporateAction(symbol="SBIN", ex_date=days[2], kind=ActionKind.SPLIT, purpose="weird")]
    )
    raw = adjusting_store.load("NSE_3045", Interval.D1, adjusted=False)
    adj = adjusting_store.load("NSE_3045", Interval.D1, adjusted=True)
    assert adj["close"].tolist() == raw["close"].tolist()


def test_corporate_actions_roundtrip(store: CandleStore) -> None:
    a = CorporateAction(
        symbol="SBIN", ex_date=date(2024, 3, 1), kind=ActionKind.SPLIT,
        price_factor=Fraction(1, 5), purpose="Face Value Split From Rs 10 To Rs 2",
    )  # fmt: skip
    assert store.upsert_corporate_actions([a, a]) == 2
    got = store.corporate_actions("SBIN")
    assert len(got) == 1 and got[0].price_factor == Fraction(1, 5) and got[0].adjusts_prices


def test_results_events(store: CandleStore) -> None:
    store.upsert_results_events(
        [
            ResultsEvent(symbol="SBIN", event_date=date(2026, 2, 5), purpose="Financial Results"),
            ResultsEvent(symbol="SBIN", event_date=date(2026, 5, 8), purpose="Financial Results"),
        ]
    )
    assert store.next_results_date("SBIN", date(2026, 1, 1)) == date(2026, 2, 5)
    assert store.next_results_date("SBIN", date(2026, 2, 6)) == date(2026, 5, 8)
    assert store.next_results_date("SBIN", date(2026, 6, 1)) is None
    cal = sessions(date(2026, 1, 26), 20)
    assert store.results_within("SBIN", date(2026, 1, 28), 10, cal) == date(2026, 2, 5)
    assert store.results_within("SBIN", date(2026, 1, 28), 3, cal) is None


def test_persists_to_file(tmp_path: Path) -> None:
    path = tmp_path / "db" / "t.duckdb"
    with CandleStore(path) as s:
        s.upsert_candles(daily("NSE_1", sessions(date(2026, 1, 5), 3)))
    with CandleStore(path) as s:
        assert s.count("NSE_1", Interval.D1) == 3


def test_indstocks_feed_default_never_double_adjusts(store: CandleStore) -> None:
    """Regression (2026-09-12): the INDstocks feed already serves split/bonus-adjusted
    history - verified against 40 real NSE corporate actions, 39 of which showed a smooth
    series across the ex-date. Before this fix, importing NSE's corporate-action export
    made `load(adjusted=True)` rescale an already-adjusted series: NESTLEIND's 1:10 split
    turned a real Rs 1,361.16 close into Rs 68.06, silently corrupting every backtest,
    scan and training run (backtest/runner.py reads with adjusted=True).

    The default store must ignore the corporate_actions table entirely, even with a real
    split sitting in it.
    """
    days = sessions(date(2026, 1, 5), 6)
    store.upsert_candles(daily("NSE_3045", days, start_price=200.0, volume=1000))
    store.upsert_corporate_actions(
        [
            CorporateAction(
                symbol="SBIN",
                ex_date=days[3],
                kind=ActionKind.SPLIT,
                price_factor=Fraction(1, 10),
                purpose="Face Value Split From Rs 10 To Re 1",
            )
        ]
    )
    raw = store.load("NSE_3045", Interval.D1, adjusted=False)
    adj = store.load("NSE_3045", Interval.D1, adjusted=True)
    assert adj["close"].tolist() == raw["close"].tolist()
    assert adj["volume"].tolist() == raw["volume"].tolist()
    assert store.corporate_actions("SBIN"), "the action is still stored, just not applied"


def test_custom_symbols_maps_scrip_code_to_the_stored_name(store: CandleStore) -> None:
    """Needed to resolve a crypto scrip_code ("CDX_BTCINR") back to CoinDCX's own `pair`
    identifier ("I-BTC_INR") - see broker/coindcx/rest.py::CoinDcxClient.candles_history."""
    crypto = Instrument(
        exch="CDX", segment="crypto", security_id="BTCINR", instrument_name="CRYPTO",
        trading_symbol="BTCINR", symbol_name="Bitcoin", series="INR",
        custom_symbol="I-BTC_INR",
    )  # fmt: skip
    store.upsert_instruments([crypto])
    assert store.custom_symbols(["CDX_BTCINR"]) == {"CDX_BTCINR": "I-BTC_INR"}
    # SBIN (the module fixture) has no custom_symbol set, so it falls back to
    # trading_symbol - present, not an empty string, so it's still returned.
    assert store.custom_symbols(["NSE_3045"]) == {"NSE_3045": "SBIN"}
    assert store.custom_symbols(["CDX_BTCINR", "NSE_NOPE"]) == {"CDX_BTCINR": "I-BTC_INR"}
    assert store.custom_symbols([]) == {}


def test_search_codes_ranks_an_exact_match_first(store: CandleStore) -> None:
    """Real bug, found running the dashboard's Lookup tab: searching the exact symbol
    "SBIN" returned "SBINEQWETF" first, because the old query ordered by scrip_code
    (a string) rather than match quality - "NSE_24524" sorts before "NSE_3045" as a
    string even though SBIN is the exact match. The dashboard's Analyze button takes
    result [0], so the wrong instrument would have been silently analyzed. Reproduces
    that exact pair: SBINEQWETF's scrip_code sorts first, but SBIN must still rank first
    in search results."""
    etf = Instrument(
        exch="NSE", segment="E", security_id="24524", instrument_name="EQUITY",
        trading_symbol="SBINEQWETF", symbol_name="SBINEQWETF", series="EQ",
        tick_size=0.05, lot_units=1,
    )  # fmt: skip
    store.upsert_instruments([etf])
    days = sessions(date(2026, 1, 5), 5)
    store.upsert_candles(daily("NSE_3045", days))  # SBIN
    store.upsert_candles(daily("NSE_24524", days))  # SBINEQWETF - scrip_code sorts first
    results = store.search_codes(Interval.D1, query="SBIN")
    assert results[0] == ("NSE_3045", "SBIN")  # exact match wins despite the code order
    assert results[1] == ("NSE_24524", "SBINEQWETF")
    # a non-exact substring query still returns both, shortest/alphabetical among ties
    assert {r[0] for r in store.search_codes(Interval.D1, query="SBI")} == {"NSE_3045", "NSE_24524"}  # noqa: E501
