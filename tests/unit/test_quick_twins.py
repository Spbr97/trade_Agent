from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from tradedesk import quick_twins as qt
from tradedesk.broker.indstocks.models import IST

ARMED = date(2026, 10, 9)  # a Friday: the twin buys Monday's open


def _store(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE candles (scrip_code VARCHAR, interval VARCHAR, ts BIGINT, open DOUBLE, "
        "high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)"
    )
    day, n = ARMED, 0
    while n < 9:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        ts = int(datetime.combine(day, datetime.min.time(), tzinfo=IST).timestamp())
        for code, start, step in (
            ("NSE_1", 100.0, 2.0),
            ("NSE_2", 50.0, -0.5),
            ("NSE_3", 10.0, 0.0),
        ):
            price = start + step * n
            con.execute(
                "INSERT INTO candles VALUES (?, '1day', ?, ?, ?, ?, ?, 1)",
                [code, ts, price, price, price, price + step],
            )
        n += 1
    con.close()


def test_twins_buy_the_next_open_and_sell_five_sessions_later(tmp_path: Path) -> None:
    (tmp_path / "data/reports").mkdir(parents=True)
    _store(tmp_path / qt.DATABASES["nse"])
    tracker = tmp_path / qt.TRACKERS["nse"]
    tracker.write_text(
        "\n".join(
            json.dumps(r)
            for r in (
                {"signal_id": "a", "setup": "r_x", "symbol": "ONE", "scrip_code": "NSE_1",
                 "armed_on": ARMED.isoformat(), "source": "live"},
                {"signal_id": "b", "setup": "r_x", "symbol": "TWO", "scrip_code": "NSE_2",
                 "armed_on": ARMED.isoformat(), "source": "backfill"},
            )
        ),
        encoding="utf-8",
    )  # fmt: skip

    assert qt.update("nse", root=tmp_path, round_trip=0.004) == 1  # backfill row ignored
    row = json.loads((tmp_path / qt.output_path("nse")).read_text().strip())
    # NSE_1 opens 100 on Mon 12 Oct and closes 100 + 2*5 + 2 = 112 five sessions later
    assert row["entry_on"] == "2026-10-12" and abs(row["net_return"] - (0.12 - 0.004)) < 1e-9
    assert row["beat_market"] is True and row["won"] is True
    assert qt.update("nse", root=tmp_path, round_trip=0.004) == 0  # idempotent
    s = qt.summary("nse", root=tmp_path)["r_x"]
    assert s["twins"] == 1 and s["winners"] == 1 and s["beat_market"] == 1


def test_an_unfinished_window_is_left_for_later() -> None:
    bars = pd.DataFrame(
        {"scrip_code": ["NSE_1"] * 3, "day": [date(2026, 10, 12 + i) for i in range(3)],
         "open": [1.0, 1.0, 1.0], "close": [1.0, 1.0, 1.0]}
    )  # fmt: skip
    assert qt.window_returns(bars, date(2026, 10, 12)) == {}
