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
