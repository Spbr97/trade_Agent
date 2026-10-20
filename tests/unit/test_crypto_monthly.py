from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tradedesk import crypto_monthly as cmo
from tradedesk.broker.indstocks.models import IST


def _db(tmp_path: Path, days: dict[str, list[tuple[datetime, float]]]) -> Path:
    path = tmp_path / "c.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE candles (scrip_code TEXT, interval TEXT, ts BIGINT, close DOUBLE)")
    for code, rows in days.items():
        for at, close in rows:
            con.execute(
                "INSERT INTO candles VALUES (?, '1day', ?, ?)", [code, int(at.timestamp()), close]
            )
    con.close()
    return path


def _bars(now: datetime, btc: float, eth: float, *, age_days: int = 1):
    day = (now - timedelta(days=age_days)).replace(hour=5, minute=30)
    today = now.replace(hour=5, minute=30)
    return {
        "CDX_BTCINR": [(day, btc), (today, btc * 2)],  # today's bar is not complete: ignored
        "CDX_ETHINR": [(day, eth), (today, eth * 2)],
    }


def test_one_buy_a_month_at_the_last_completed_close(tmp_path: Path) -> None:
    now = datetime(2026, 11, 1, 8, 10, tzinfo=IST)
    db, ledger = _db(tmp_path, _bars(now, 8_000_000.0, 300_000.0)), tmp_path / "plan.jsonl"
    first = cmo.run(db=db, ledger=ledger, now=now)
    assert [b["coin"] for b in first["bought"]] == ["BTC", "ETH"]
    assert first["bought"][0]["price"] == 8_000_000.0
    assert cmo.message(first) is not None
    again = cmo.run(db=db, ledger=ledger, now=now + timedelta(days=1))
    assert again["bought"] == [] and cmo.message(again) is None
    assert len(cmo.read(ledger)) == 2


def test_stale_prices_record_nothing(tmp_path: Path) -> None:
    now = datetime(2026, 11, 1, 8, 10, tzinfo=IST)
    db = _db(tmp_path, _bars(now, 8_000_000.0, 300_000.0, age_days=5))
    result = cmo.run(db=db, ledger=tmp_path / "plan.jsonl", now=now)
    assert result["stale"] and result["bought"] == []


def test_value_if_sold_takes_off_what_the_sale_cuts_but_no_year_end_tax() -> None:
    win = {"code": "A", "amount_inr": 5000.0, "qty": 1.0}
    lose = {"code": "B", "amount_inr": 5000.0, "qty": 1.0}
    t = cmo.value([win, lose], {"A": 10_000.0, "B": 2_500.0})
    sell = (1 - cmo.SLIP) * (1 - cmo.FEE)
    assert t["if_sold"] == pytest.approx(12_500 * sell - 12_500 * cmo.TDS)
    assert t["invested"] == 10_000 and t["worth"] == 12_500
