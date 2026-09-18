import duckdb
from tradedesk_lab.mcb_data_audit import audit_mcb_data


def test_reference_coverage_audit_is_read_only_and_reports_missing_history(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    database = data / "tradedesk.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute(
            "CREATE TABLE instruments(scrip_code VARCHAR,trading_symbol VARCHAR,"
            "symbol_name VARCHAR,exch VARCHAR)"
        )
        con.execute("INSERT INTO instruments VALUES ('NSE_1','GOODLUCK','Goodluck India','NSE')")
        con.execute(
            "CREATE TABLE candles(scrip_code VARCHAR,interval VARCHAR,ts BIGINT,"
            "open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume BIGINT)"
        )
        con.execute("INSERT INTO candles VALUES ('NSE_1','1day',1,1,1,1,1,1)")
    original = database.read_bytes()
    output = tmp_path / "lab"
    report = audit_mcb_data(tmp_path, output)
    goodluck = report["references"][0]
    assert len(goodluck["matches"]) == 1
    assert goodluck["matches"][0]["trading_symbol"] == "GOODLUCK"
    assert goodluck["matches"][0]["coverage"][0]["interval"] == "1day"
    assert report["interval_summary"] == [{"interval": "1day", "symbols": 1, "bars": 1}]
    assert database.read_bytes() == original
    assert (output / "mcb/data-audit.json").exists()
