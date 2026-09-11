from __future__ import annotations

from datetime import date

import pytest

from tests.data.synth import daily, sessions
from tradedesk.broker.indstocks.models import IndexInstrument
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.results_calendar import (
    is_results_purpose,
    parse_nse_board_meetings_csv,
    sessions_until,
)
from tradedesk.data.universe import UniverseRules, month_starts, trading_days, universe_on

NIFTY = IndexInstrument(exch="NSE", name="NIFTY 50", security_id="40000001")
REF = NIFTY.scrip_code


@pytest.fixture
def store() -> CandleStore:
    with CandleStore() as s:
        s.upsert_instruments([NIFTY])
        yield s


def test_trading_days_come_from_reference_index(store: CandleStore) -> None:
    days = sessions(date(2026, 1, 1), 30)
    holiday = days[10]
    store.upsert_candles(daily(REF, [d for d in days if d != holiday], start_price=25000))
    cal = trading_days(store, REF, days[0], days[-1])
    assert holiday not in cal and len(cal) == 29
    assert cal == sorted(cal)


def test_universe_on_applies_turnover_price_and_history_rules(store: CandleStore) -> None:
    days = sessions(date(2026, 1, 1), 40)
    # liquid: 100 x 1,000,000 = 1e8 turnover/day
    store.upsert_candles(daily("NSE_LIQ", days, start_price=100, volume=1_000_000))
    # illiquid: 100 x 100,000 = 1e7 < 5e7
    store.upsert_candles(daily("NSE_THIN", days, start_price=100, volume=100_000))
    # penny: high turnover but price below floor
    store.upsert_candles(daily("NSE_PENNY", days, start_price=20, volume=50_000_000))
    # new listing: only 10 sessions of history
    store.upsert_candles(daily("NSE_NEW", days[-10:], start_price=100, volume=1_000_000))
    candidates = ["NSE_LIQ", "NSE_THIN", "NSE_PENNY", "NSE_NEW"]
    assert universe_on(store, candidates, days[-1]) == ["NSE_LIQ"]
    # As of an earlier date the new listing has no bars at all, and NSE_LIQ still qualifies.
    assert universe_on(store, candidates, days[25]) == ["NSE_LIQ"]
    loose = UniverseRules(min_avg_turnover_inr=1e6, min_price=1, lookback_sessions=5, min_sessions_present=5)
    assert universe_on(store, candidates, days[-1], loose) == sorted(candidates)
    assert universe_on(store, [], days[-1]) == []


def test_universe_uses_only_data_up_to_the_date(store: CandleStore) -> None:
    days = sessions(date(2026, 1, 1), 40)
    # Volume dries up in the last 20 sessions: liquid early, illiquid late.
    early = daily("NSE_X", days[:20], start_price=100, volume=1_000_000)
    late = daily("NSE_X", days[20:], start_price=100, volume=1_000)
    store.upsert_candles(early + late)
    assert universe_on(store, ["NSE_X"], days[19]) == ["NSE_X"]
    assert universe_on(store, ["NSE_X"], days[-1]) == []


def test_month_starts() -> None:
    cal = sessions(date(2026, 1, 1), 60)
    starts = month_starts(cal)
    assert starts[0] == date(2026, 1, 1)
    assert all(d.day <= 3 for d in starts)
    assert [d.month for d in starts] == [1, 2, 3]


BOARD_CSV = """Symbol,Company Name,Purpose,Details,BoardMeeting Date
SBIN,State Bank of India,Financial Results,To consider Q2 results,08-Nov-2026
INFY,Infosys Ltd,Fund Raising,Issue of NCDs,10-Nov-2026
TCS,Tata Consultancy,Financial Results/Dividend,,09-Oct-2026
"""


def test_board_meetings_csv_results_only() -> None:
    events = parse_nse_board_meetings_csv(BOARD_CSV)
    assert [(e.symbol, e.event_date) for e in events] == [
        ("SBIN", date(2026, 11, 8)),
        ("TCS", date(2026, 10, 9)),
    ]
    assert len(parse_nse_board_meetings_csv(BOARD_CSV, results_only=False)) == 3
    assert is_results_purpose("Audited Financial Results") and not is_results_purpose("Buyback")


def test_sessions_until() -> None:
    cal = sessions(date(2026, 1, 5), 10)
    assert sessions_until(cal[4], cal[1], cal) == 3
    assert sessions_until(None, cal[1], cal) is None
    assert sessions_until(cal[1], cal[1], cal) == 0
