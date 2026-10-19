"""The owner's real fills: only filled equity orders count, a BUY of a called stock on NSE or BSE
is that call's entry, a later SELL closes it, nothing is recorded twice, and pushes itemise only
our calls."""

from __future__ import annotations

from pathlib import Path

from tradedesk import earnings_drift as ed
from tradedesk import owner_trades as ot

ISINS = {
    ("NSE", "11534"): {"isin": "INE669C01036", "symbol": "TECHM"},
    ("BSE", "532755"): {"isin": "INE669C01036", "symbol": "TECHM"},
    ("NSE", "3787"): {"isin": "INE075A01022", "symbol": "WIPRO"},
    ("NSE", "1594"): {"isin": "INE009A01021", "symbol": "INFY"},
}
CALLS = {
    "TECHM:2026-10-15": {"call_id": "TECHM:2026-10-15", "scrip_code": "NSE_11534",
                         "at": "2026-10-18T09:38:14+05:30", "reference_close": 1790.0},
    "WIPRO:2026-10-15": {"call_id": "WIPRO:2026-10-15", "scrip_code": "NSE_3787",
                         "at": "2026-10-18T09:38:14+05:30", "reference_close": 184.4},
}  # fmt: skip


def _order(oid, exch, sid, side, qty, price, *, segment="EQUITY", at="2026-10-19T09:15:30+05:30"):
    return {"id": oid, "exchange": exch, "security_id": sid, "txn_type": side, "segment": segment,
            "traded_qty": qty, "traded_price": str(price), "status": "SUCCESS",
            "updated_at": at, "name": "x", "isin": ""}  # fmt: skip


def test_only_filled_equity_orders_count() -> None:
    rows = [
        _order("1", "NSE", "11534", "BUY", 5, 1795.2),
        _order("2", "NSE", "3787", "BUY", 0, 0),  # cancelled: nothing traded
        _order("3", "NSE", "56998", "BUY", 65, 43.5, segment="DERIVATIVE"),
    ]
    fills = ot.executed(rows, ISINS)
    assert [f["order_id"] for f in fills] == ["1"]
    assert fills[0]["isin"] == "INE669C01036" and fills[0]["symbol"] == "TECHM"


def test_a_buy_on_bse_still_enters_the_nse_call_and_a_sell_closes_it() -> None:
    buy = ot.executed([_order("1", "BSE", "532755", "BUY", 5, 1799.0)], ISINS)
    events = ot.match(buy, CALLS, ISINS, [])
    assert events[0]["kind"] == "owner_entered" and events[0]["call_id"] == "TECHM:2026-10-15"
    assert round(events[0]["vs_call_close"], 4) == round(1799 / 1790 - 1, 4)
    assert ot.open_drift_positions(events) == 1

    later = "2026-11-16T15:20:00+05:30"
    sell = ot.executed([_order("9", "NSE", "11534", "SELL", 5, 1890.0, at=later)], ISINS)
    closed = ot.match(sell, CALLS, ISINS, events)
    assert closed[0]["kind"] == "owner_exited" and closed[0]["gross_inr"] == 5 * (1890 - 1799)
    assert ot.open_drift_positions([*events, *closed]) == 0


def test_nothing_is_recorded_twice_and_other_trades_stay_other() -> None:
    rows = [
        _order("1", "NSE", "11534", "BUY", 5, 1795.2),
        _order("2", "NSE", "1594", "BUY", 3, 1500),
    ]
    first = ot.match(ot.executed(rows, ISINS), CALLS, ISINS, [])
    assert [e["kind"] for e in first] == ["owner_entered", "other"]
    assert ot.match(ot.executed(rows, ISINS), CALLS, ISINS, first) == []  # same orders again


def test_a_buy_long_after_the_call_is_not_that_call() -> None:
    late = ot.executed([_order("1", "NSE", "11534", "BUY", 5, 1795.2,
                               at="2026-11-30T10:00:00+05:30")], ISINS)  # fmt: skip
    assert ot.match(late, CALLS, ISINS, [])[0]["kind"] == "other"


def test_the_push_itemises_our_calls_only() -> None:
    rows = [
        _order("1", "NSE", "11534", "BUY", 5, 1795.2),
        _order("2", "NSE", "1594", "BUY", 3, 1500),
    ]
    events = ot.match(ot.executed(rows, ISINS), CALLS, ISINS, [])
    title, body = ot.message(events, 1, 10)
    assert title == "Your trades today"
    assert "BUY TECHM 5 @ Rs 1,795.20 on NSE: earnings-drift call taken" in body
    assert "INFY" not in body and "+1 other trade(s) recorded" in body
    assert "1 of 10 slots" in body


def test_drift_calls_show_the_slots_in_use() -> None:
    result = {"issued": [{"symbol": "TECHM", "reference_close": 1790.0, "jump_vs_market": 0.14,
                          "volume_x": 8.1}], "exit_tomorrow": [], "exited": [],
              "verdict": {"status": "collecting"}}  # fmt: skip
    body = ed.messages(result, held=3, slots=10)[0][1]
    assert "Your slots: 3 of 10 in use, 7 free" in body
    assert "Your slots" not in ed.messages(result)[0][1]  # no sync yet: no claim


def test_the_instrument_master_maps_both_exchanges(tmp_path: Path) -> None:
    csv_path = tmp_path / "equity.csv"
    csv_path.write_text(
        "EXCH,SECURITY_ID,TRADING_SYMBOL,ISIN\nNSE,1594,INFY,INE009A01021\n"
        "BSE,500209,INFY,INE009A01021\nNSE,999,NOISIN,\n",
        encoding="utf-8",
    )
    m = ot.isin_map(csv_path)
    assert m[("NSE", "1594")]["isin"] == m[("BSE", "500209")]["isin"]
    assert ("NSE", "999") not in m
