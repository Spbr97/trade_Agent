from tradedesk.broker.indstocks.instruments import (
    nse_cash_equities,
    parse_index_csv,
    parse_instruments_csv,
)

HEADER = (
    "EXCH,SEGMENT,SECURITY_ID,INSTRUMENT_NAME,EXPIRY_CODE,TRADING_SYMBOL,LOT_UNITS,"
    "CUSTOM_SYMBOL,EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE,EXPIRY_FLAG,"
    "SEM_EXCH_INSTRUMENT_TYPE,SERIES,SYMBOL_NAME\n"
)
ROWS = [
    "NSE,E,2885,EQUITY,0,RELIANCE,1,Reliance Industries,,,,0.05,,ES,EQ,RELIANCE",
    "NSE,E,3045,EQUITY,0,SBIN,1,State Bank of India,,,,0.05,,ES,EQ,SBIN",
    "BSE,E,500325,EQUITY,0,RELIANCE,1,Reliance Industries,,,,0.05,,ES,A,RELIANCE",
    "NSE,E,99999,EQUITY,0,SOMEBE,1,Some BE stock,,,,0.05,,ES,BE,SOMEBE",
    "NSE,E,,EQUITY,0,BLANKID,1,,,,,,,ES,EQ,BLANKID",
]
EQUITY_CSV = HEADER + "\n".join(ROWS) + "\n"

INDEX_CSV = """EXCH,SEGMENT,SECURITY_ID
NSE,NIFTY 50,40000001
NSE,NIFTY IT,40000004
BSE,BSE Focused IT,40000129
"""


def test_parse_equity_csv_by_header_name() -> None:
    rows = parse_instruments_csv(EQUITY_CSV)
    assert [r.security_id for r in rows] == ["2885", "3045", "500325", "99999"]  # blank skipped
    r = rows[0]
    assert r.scrip_code == "NSE_2885" and r.ws_code == "NSE:2885"
    assert r.tick_size == 0.05 and r.lot_units == 1 and r.symbol_name == "RELIANCE"
    assert r.is_cash_equity


def test_nse_cash_equities_filters_exchange_and_series() -> None:
    rows = nse_cash_equities(parse_instruments_csv(EQUITY_CSV))
    assert [r.trading_symbol for r in rows] == ["RELIANCE", "SBIN"]  # BSE and BE dropped


def test_parse_index_csv_positionally() -> None:
    idx = parse_index_csv(INDEX_CSV)
    assert [(i.exch, i.name, i.security_id) for i in idx] == [
        ("NSE", "NIFTY 50", "40000001"),
        ("NSE", "NIFTY IT", "40000004"),
        ("BSE", "BSE Focused IT", "40000129"),
    ]
    assert idx[0].scrip_code == "NSE_40000001"


def test_empty_csv() -> None:
    assert parse_instruments_csv("") == []
    assert parse_index_csv("") == []
