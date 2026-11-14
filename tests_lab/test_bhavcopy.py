"""bhavcopy: parsing both layouts, renames, split adjustment and the resumable download."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import bhavcopy as bc

OLD_TEXT = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"  # noqa: E501
    "IRCTC,EQ,817,983,810,913.5,923,4130.15,59298778,54875931755.65,28-OCT-2021,1928611,INE335Y01012,\n"  # noqa: E501
    "TINY,SM,10,11,9,10.5,10.5,10,100,1050,28-OCT-2021,3,INE000000001,\n"
    "WEAK,BE,50,52,49,51,51,50,1000,51000,28-OCT-2021,30,INE000000002,\n"
)
NEW_HEAD = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,"
    "PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,"
    "TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd01,Rsvd02,Rsvd03,Rsvd04,\n"
)
NEW_TEXT = NEW_HEAD + (
    "2024-07-08,2024-07-08,CM,NSE,STK,1,INE1,ABC,EQ,,,,,ABC LTD,100,110,99,105,105,101,,105,,,"
    "5000,525000,40,F1,1,,,,,,\n"
    "2024-07-08,2024-07-08,CM,NSE,STK,2,IN0020170067,SGBX,GB,,,,,GOLD,6180,6422,6131,6422,6422,"
    "6131,,6422,,,23,142629,6,F1,1,,,,,,\n"
)


def test_parse_old_layout_keeps_mainboard_series_only() -> None:
    rows = bc.parse(OLD_TEXT, date(2021, 10, 28))
    assert [r["symbol"] for r in rows] == ["IRCTC", "WEAK"]
    irctc = rows[0]
    assert irctc["prevclose"] == 4130.15 and irctc["close"] == 913.5
    assert irctc["volume"] == 59298778 and irctc["day"] == "2021-10-28"


def test_parse_new_layout() -> None:
    rows = bc.parse(NEW_TEXT, date(2024, 7, 8))
    assert len(rows) == 1
    assert rows[0] | {} == {
        "day": "2024-07-08", "symbol": "ABC", "series": "EQ", "isin": "INE1", "open": 100.0,
        "high": 110.0, "low": 99.0, "close": 105.0, "prevclose": 101.0, "volume": 5000.0,
        "turnover": 525000.0,
    }  # fmt: skip


def test_parse_refuses_an_unknown_layout() -> None:
    with pytest.raises(ValueError):
        bc.parse("A,B\n1,2\n", date(2020, 1, 1))


def test_urls_try_the_format_of_the_period_first() -> None:
    old_first = bc.urls(date(2018, 3, 1))
    assert old_first[0].endswith("/2018/MAR/cm01MAR2018bhav.csv.zip")
    assert old_first[1].endswith("_20180301_F_0000.csv.zip")
    assert bc.urls(date(2024, 7, 8))[0].endswith("_20240708_F_0000.csv.zip")


CHANGES = [("OLDA", "MIDA", date(2018, 1, 10)), ("MIDA", "NEWA", date(2020, 5, 1)),
           ("REUSE", "OTHER", date(2019, 1, 1))]  # fmt: skip


def test_canonical_follows_a_chain_of_renames() -> None:
    assert bc.canonical("OLDA", date(2017, 6, 1), CHANGES) == "NEWA"
    assert bc.canonical("MIDA", date(2019, 6, 1), CHANGES) == "NEWA"
    assert bc.canonical("NEWA", date(2021, 6, 1), CHANGES) == "NEWA"


def test_canonical_does_not_merge_a_reused_symbol() -> None:
    assert bc.canonical("REUSE", date(2018, 6, 1), CHANGES) == "OTHER"
    assert bc.canonical("REUSE", date(2022, 6, 1), CHANGES) == "REUSE"


def test_read_changes(tmp_path: Path) -> None:
    path = tmp_path / "c.csv"
    path.write_text("NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"
                    " Foo Ltd,FOO,BAR,30-OCT-2019\n bad row\n Baz,BAZ,QUX,02-JAN-2017\n")
    assert bc.read_changes(path) == [("BAZ", "QUX", date(2017, 1, 2)),
                                     ("FOO", "BAR", date(2019, 10, 30))]  # fmt: skip


def _stock(opens: list[float], closes: list[float]) -> pd.DataFrame:
    days = pd.date_range("2021-10-25", periods=len(closes), freq="B")
    return pd.DataFrame({
        "day": days, "open": opens, "high": np.array(closes) * 1.01,
        "low": np.array(closes) * 0.99, "close": closes, "prevclose": np.nan,
        "volume": [1000.0] * len(closes),
    })  # fmt: skip


def test_adjust_applies_a_recorded_split_to_earlier_rows() -> None:
    stock = _stock([4200, 4250, 817, 900], [4189.5, 4130.15, 913.5, 920])
    out, notes = bc.adjust(stock, {date(2021, 10, 27): 0.2})
    assert out["close"].round(2).tolist() == [837.9, 826.03, 913.5, 920]
    assert out["volume"].tolist() == [5000.0, 5000.0, 1000.0, 1000.0]
    assert notes == ["2021-10-27 recorded x0.2000"]


def test_adjust_finds_an_unrecorded_deep_split() -> None:
    stock = _stock([400, 402, 201.5, 205], [401, 400, 204, 206])
    out, notes = bc.adjust(stock, {})
    assert notes == ["2021-10-27 unrecorded x0.5000"]
    assert out["close"].tolist()[:2] == [200.5, 200.0]


def test_adjust_leaves_a_circuit_fall_and_a_crash_alone() -> None:
    circuit = _stock([100, 80, 64], [100, 80, 64])  # two 20% lower circuits
    crash = _stock([100, 41, 40], [100, 40, 39])  # a 59% gap, near no ratio
    for stock in (circuit, crash):
        out, notes = bc.adjust(stock, {})
        assert notes == [] and out["close"].tolist() == stock["close"].tolist()


def test_one_row_a_day_prefers_eq() -> None:
    frame = pd.DataFrame({
        "name": ["A", "A", "A"], "day": pd.to_datetime(["2020-01-01"] * 2 + ["2020-01-02"]),
        "series": ["BE", "EQ", "BE"], "close": [1.0, 2.0, 3.0],
    })  # fmt: skip
    assert bc.one_row_a_day(frame)["close"].tolist() == [2.0, 3.0]


def _zipped(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("f.csv", text)
    return buffer.getvalue()


def test_fetch_saves_records_missing_and_resumes(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "cm28OCT2021bhav" in url:
            return httpx.Response(200, content=_zipped(OLD_TEXT))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    days = [date(2021, 10, 28), date(2021, 10, 29)]
    counts = bc.fetch(days, store=tmp_path, pause_seconds=0, client=client)
    assert counts == {"saved": 1, "missing": 1, "already": 0}
    assert (tmp_path / "missing.txt").read_text().split() == ["2021-10-29"]
    rows = bc.read_rows(tmp_path)
    assert rows["symbol"].tolist() == ["IRCTC", "WEAK"] and rows["close"].tolist() == [913.5, 51.0]
    calls.clear()
    assert bc.fetch(days, store=tmp_path, pause_seconds=0, client=client)["already"] == 2
    assert calls == []
