"""Dynamic CoinDCX monitoring universe.

The public instruments endpoint is the authority for what is active now.  Keeping this
selection separate from trade eligibility is deliberate: every active INR pair is
monitored, while liquidity, stale-data, setup and accuracy gates remain free to reject it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, time

from tradedesk.broker.indstocks.models import IST, Instrument, Interval
from tradedesk.data.candle_store import CandleStore


def active_crypto_codes(instruments: Iterable[Instrument]) -> list[str]:
    """Return every active INR pair supplied by CoinDCX, deterministically ordered.

    ``CoinDcxClient.instruments()`` already filters the remote master to active INR spot
    markets.  The defensive exchange/series checks prevent a future adapter change from
    silently widening this to non-INR or non-crypto instruments.
    """
    return sorted(
        {
            instrument.scrip_code
            for instrument in instruments
            if instrument.exch == "CDX" and instrument.series == "INR"
        }
    )


def stored_crypto_codes(store: CandleStore) -> list[str]:
    """Offline equivalent used by historical backfills after instruments were synced."""
    return store.instrument_codes(kind="equity", exch="CDX", series="INR")


def crypto_codes_needing_daily_refresh(
    store: CandleStore, codes: Iterable[str], now: datetime
) -> list[str]:
    """Pairs without the current UTC-day candle.

    The scheduled task already performs a full incremental load immediately before the
    tracker.  Restricting the tracker's own fetch to missing/stale pairs avoids repeating
    hundreds of API calls while still bootstrapping a newly listed pair discovered by the
    fresh instrument-master request.
    """
    utc_day = now.astimezone(UTC).date()
    expected_open = datetime.combine(utc_day, time.min, tzinfo=UTC).astimezone(IST)
    return sorted(
        code
        for code in codes
        if (last := store.last_ts(code, Interval.D1)) is None or last < expected_open
    )
