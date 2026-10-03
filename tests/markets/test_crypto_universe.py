from datetime import UTC, datetime

from tradedesk.broker.indstocks.models import Instrument
from tradedesk.markets.crypto_universe import (
    active_crypto_codes,
    crypto_codes_needing_daily_refresh,
)


def instrument(code: str, *, exch: str = "CDX", series: str = "INR") -> Instrument:
    return Instrument(
        exch=exch,
        segment="crypto",
        security_id=code,
        instrument_name="CRYPTO",
        trading_symbol=code,
        series=series,
        custom_symbol=f"I-{code[:-3]}_{code[-3:]}",
    )


def test_active_crypto_codes_keeps_complete_inr_population_sorted_and_unique() -> None:
    instruments = [
        instrument("ETHINR"),
        instrument("BTCINR"),
        instrument("BTCINR"),
        instrument("BTCUSDT", series="USDT"),
        instrument("FAKEINR", exch="NSE"),
    ]

    assert active_crypto_codes(instruments) == ["CDX_BTCINR", "CDX_ETHINR"]


class Store:
    def __init__(self, timestamps):
        self.timestamps = timestamps

    def last_ts(self, code, interval):
        return self.timestamps.get(code)


def test_daily_refresh_only_requests_new_or_stale_pairs() -> None:
    now = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)
    store = Store(
        {
            "CDX_CURRENTINR": datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
            "CDX_STALEINR": datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        }
    )

    assert crypto_codes_needing_daily_refresh(
        store, ["CDX_CURRENTINR", "CDX_STALEINR", "CDX_NEWINR"], now
    ) == ["CDX_NEWINR", "CDX_STALEINR"]
