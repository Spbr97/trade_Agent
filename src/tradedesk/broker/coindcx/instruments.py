"""Parse /exchange/v1/markets_details into the shared Instrument model (M13 plan §5, §6).

Reuses tradedesk.broker.indstocks.models.Instrument as-is rather than defining a new
crypto instrument type: Instrument.scrip_code is `f"{exch}_{security_id}"`, so
exch="CDX", security_id=coindcx_name ("BTCINR") gives exactly the "CDX_BTCINR" prefix
the plan specifies, with zero changes to CandleStore's instruments table or upsert path.
"""

from __future__ import annotations

from typing import Any

from tradedesk.broker.indstocks.models import Instrument

EXCH = "CDX"


def parse_markets_details(raw: list[dict[str, Any]]) -> list[Instrument]:
    """Active, INR-quoted spot markets only (997 total rows observed 2026-09-12; 338
    matched this filter). `pair` (e.g. "I-BTC_INR") is what the candles endpoint wants -
    kept as `custom_symbol` since Instrument has no dedicated field for it."""
    out: list[Instrument] = []
    for m in raw:
        if m.get("status") != "active":
            continue
        if m.get("base_currency_short_name") != "INR":
            continue
        name = m.get("coindcx_name")
        pair = m.get("pair")
        if not name or not pair:
            continue
        out.append(
            Instrument(
                exch=EXCH,
                segment="crypto",
                security_id=str(name),
                instrument_name="CRYPTO",
                trading_symbol=str(name),
                symbol_name=str(
                    m.get("target_currency_name") or m.get("target_currency_short_name") or name
                ),
                series="INR",
                tick_size=_num(m.get("step")),
                lot_units=None,
                custom_symbol=str(pair),
            )
        )
    return out


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
