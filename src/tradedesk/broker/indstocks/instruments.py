"""Parse the instrument-master CSVs from /market/instruments (docs/indstocks-api.md).

`source=equity|fno` → 16 named columns. `source=index` → 3 columns where the header says
SEGMENT but the column holds the index name, so it is read positionally.
"""

from __future__ import annotations

import csv
import io

from tradedesk.broker.indstocks.models import IndexInstrument, Instrument


def _num(v: str | None) -> float | None:
    if v is None or v.strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_instruments_csv(text: str) -> list[Instrument]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return []
    cols = {c.strip().upper(): c for c in reader.fieldnames}

    def get(row: dict[str, str], name: str) -> str:
        key = cols.get(name)
        return (row.get(key) or "").strip() if key else ""

    out: list[Instrument] = []
    for row in reader:
        if not get(row, "SECURITY_ID"):
            continue
        lot = _num(get(row, "LOT_UNITS"))
        out.append(
            Instrument(
                exch=get(row, "EXCH"),
                segment=get(row, "SEGMENT"),
                security_id=get(row, "SECURITY_ID"),
                instrument_name=get(row, "INSTRUMENT_NAME"),
                trading_symbol=get(row, "TRADING_SYMBOL"),
                symbol_name=get(row, "SYMBOL_NAME"),
                series=get(row, "SERIES"),
                tick_size=_num(get(row, "TICK_SIZE")),
                lot_units=int(lot) if lot is not None else None,
                custom_symbol=get(row, "CUSTOM_SYMBOL"),
                expiry_date=get(row, "EXPIRY_DATE"),
                strike_price=_num(get(row, "STRIKE_PRICE")),
                option_type=get(row, "OPTION_TYPE"),
            )
        )
    return out


def parse_index_csv(text: str) -> list[IndexInstrument]:
    rows = list(csv.reader(io.StringIO(text)))
    out: list[IndexInstrument] = []
    for i, row in enumerate(rows):
        if i == 0 and row and row[0].strip().upper() == "EXCH":
            continue  # header
        if len(row) < 3 or not row[2].strip():
            continue
        out.append(
            IndexInstrument(exch=row[0].strip(), name=row[1].strip(), security_id=row[2].strip())
        )
    return out


def nse_cash_equities(instruments: list[Instrument]) -> list[Instrument]:
    return [i for i in instruments if i.exch.upper() == "NSE" and i.is_cash_equity]


def bse_cash_equities(instruments: list[Instrument]) -> list[Instrument]:
    """Same instrument-master CSV as NSE (`source=equity` returns both exchanges in one
    file - see docs/indstocks-api.md's Get Instrument List), filtered to EXCH=BSE.

    Can't reuse `is_cash_equity` (series == "EQ"): BSE has no single equity series like
    NSE, it uses GROUP codes instead. Sampled real rows 2026-09-12 (12,880 BSE "EQUITY"
    rows, 10 distinct series): A (PIIND, AVANTEL, NATIONALUM - large/liquid) and B
    (MOM50, JAYAGROGN - the rest of the regular cash segment) are genuine actively-traded
    equities. F (796PIL29, KTKFMP48D) and G (GS06NOV58, SGBJAN30IX) are coupon/maturity-
    coded bonds and government securities respectively - the WRONG ASSET CLASS entirely,
    not just illiquid, so they must never enter a technical-analysis universe. X/XT/M/MT/T
    are real equities but restricted/SME/trade-for-trade subgroups - excluded from v1 as a
    deliberate simplification (existing turnover/price floors would filter most of them
    out anyway); revisit only if BSE's universe needs to grow past A/B."""
    return [
        i
        for i in instruments
        if i.exch.upper() == "BSE"
        and i.instrument_name.upper() == "EQUITY"
        and i.series.upper() in {"A", "B"}
    ]
