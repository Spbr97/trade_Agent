from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradedesk import earnings_drift as ed
from tradedesk.broker.indstocks.models import IST


def _panels(n_sessions: int = 30, *, jump: float = 0.12, volume_x: float = 4.0) -> ed.Panels:
    """30 weekday sessions, 12 flat liquid stocks plus JUMPY, which reports on the day
    before the last session and closes `jump` higher on `volume_x` its usual volume."""

    days, d = [], date(2026, 9, 1)
    while len(days) < n_sessions:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    codes = [f"NSE_{i}" for i in range(12)] + ["NSE_J"]
    close = pd.DataFrame(500.0, index=days, columns=codes)
    volume = pd.DataFrame(1_000_000.0, index=days, columns=codes)
    close.iloc[-1, -1] = 500.0 * (1 + jump)
    volume.iloc[-1, -1] = 1_000_000.0 * volume_x
    symbols = {f"S{i}": f"NSE_{i}" for i in range(12)} | {"JUMPY": "NSE_J"}
    events = pd.DataFrame({"symbol": ["JUMPY"], "event_date": [days[-2] + timedelta(days=0)]})
    events.loc[0, "event_date"] = (
        days[-1] - timedelta(days=1) if days[-1].weekday() > 0 else days[-2]
    )
    return ed.Panels(close.copy(), close, volume, symbols, events)


def test_a_liquid_results_jump_on_volume_is_a_call() -> None:
    found = ed.candidates(_panels())
    assert [c["symbol"] for c in found] == ["JUMPY"]
    assert abs(found[0]["jump_vs_market"] - 0.12) < 0.02 and found[0]["volume_x"] >= 2


def test_a_call_made_on_a_market_down_day_is_marked_weaker() -> None:
    calm = ed.candidates(_panels())[0]
    assert calm["down_day"] is False and calm["market_day_move"] == pytest.approx(0.12 / 13)
    p = _panels()
    p.close.iloc[-1, :12] = 500.0 * 0.97  # the rest of the market falls 3% that session
    falling = ed.candidates(p)[0]
    assert falling["down_day"] is True
    assert falling["market_day_move"] == pytest.approx((12 * -0.03 + 0.12) / 13)
    assert falling["symbol"] == "JUMPY"  # still a call: the mark never withholds one


def test_calls_from_before_the_mark_are_annotated_once(tmp_path: Path) -> None:
    p = _panels()
    p.close.iloc[-1, :12] = 500.0 * 0.97
    old = {"event": "call", "call_id": "JUMPY:x", "at": "t", "symbol": "JUMPY",
           "scrip_code": "NSE_J", "reaction_close_on": p.sessions[-1].isoformat()}  # fmt: skip
    marks = ed.annotate(p, [old], datetime.now(IST))
    assert [m["event"] for m in marks] == ["annotated"] and marks[0]["down_day"] is True
    merged = ed.positions([old, *marks])["JUMPY:x"]
    assert merged["down_day"] is True and merged["symbol"] == "JUMPY"
    assert ed.annotate(p, [old, *marks], datetime.now(IST)) == []


def test_the_call_push_says_a_down_day_call_is_weaker() -> None:
    call = {"symbol": "CYIENT", "reference_close": 1200.0, "jump_vs_market": 0.09, "volume_x": 3.0}
    result = {"issued": [call, {**call, "symbol": "GRWRHITECH", "down_day": True}],
              "exit_tomorrow": [], "exited": [], "verdict": {"status": "live"}}  # fmt: skip
    body = ed.messages(result)[0][1]
    assert body.count("DOWN DAY - weaker") == 1 and "GRWRHITECH BUY" in body
    assert "only +0.2% over the market" in body and "With real money, skip them" in body
    plain = ed.messages({**result, "issued": [call]})[0][1]
    assert "DOWN DAY" not in plain


def test_small_jumps_or_quiet_volume_are_not_calls() -> None:
    assert ed.candidates(_panels(jump=0.03)) == []
    assert ed.candidates(_panels(volume_x=1.2)) == []


def test_a_call_is_entered_at_the_real_open_and_closed_after_20_sessions(tmp_path: Path) -> None:
    p = _panels()
    ledger = tmp_path / "calls.jsonl"
    call = ed.candidates(p)[0]
    ed._append([{"event": "call", "call_id": "JUMPY:x", "at": "t", **call}], ledger)
    # extend the market 20 more sessions with JUMPY drifting up 4%
    extra, d = [], p.sessions[-1] + timedelta(days=1)
    while len(extra) < 20:
        if d.weekday() < 5:
            extra.append(d)
        d += timedelta(days=1)
    close = pd.concat([p.close, pd.DataFrame(500.0, index=extra, columns=p.close.columns)])
    close.loc[extra, "NSE_J"] = np.linspace(560, 582.4, 20)
    opens = close.copy()
    longer = ed.Panels(
        opens,
        close,
        pd.concat([p.volume, p.volume.iloc[-20:].set_axis(extra)]),
        p.symbols,
        p.events,
    )

    moves = ed.advance(longer, ed.read_ledger(ledger), datetime.now(IST))
    entered = next(m for m in moves if m["event"] == "entered")
    exited = next(m for m in moves if m["event"] == "exited")
    assert entered["entry_on"] == extra[0].isoformat() and entered["entry_price"] == 560.0
    assert entered["qty"] == int(10_000 // 560)
    assert exited["exit_on"] == extra[19].isoformat()  # the 20th session, entry day counted
    assert exited["excess_return"] > 0.03 and exited["net_return"] < exited["excess_return"]


def test_new_calls_stop_when_live_results_trail_the_market() -> None:
    losing = [
        {"event": "call", "call_id": f"X{i}:d", "at": "t", "symbol": f"X{i}", "scrip_code": f"C{i}"}
        for i in range(25)
    ] + [
        {"event": "exited", "call_id": f"X{i}:d", "at": "t", "exit_on": f"2026-10-{1 + i % 28:02d}",
         "net_return": -0.04 - 0.001 * i, "excess_return": -0.03 - 0.001 * i, "net_inr": -400.0}
        for i in range(25)
    ]  # fmt: skip
    assert ed.verdict(losing)["status"] == "stopped"
    assert ed.verdict(losing[:30])["status"] == "live"  # too few closed calls to judge


def test_messages_name_the_stock_amount_and_exit_rule() -> None:
    result = {
        "issued": [{"symbol": "TECHM", "reference_close": 1790.0, "jump_vs_market": 0.144, "volume_x": 8.1}],  # noqa: E501
        "exit_tomorrow": ["WIPRO"], "exited": [], "verdict": {"status": "live"}, "last_session": "x",  # noqa: E501
    }  # fmt: skip
    msgs = dict(ed.messages(result))
    body = msgs["EARNINGS DRIFT: 1 call(s) for the next open"]
    assert "TECHM BUY at next open" in body and "~5 sh for Rs 10,000" in body and "+14.4%" in body
    assert msgs["EARNINGS DRIFT: exit tomorrow at the close"] == "WIPRO"


def test_each_call_says_whether_the_market_is_above_its_200_day_average() -> None:
    from datetime import date as _date
    from datetime import timedelta as _td

    import pandas as _pd

    from tradedesk import earnings_drift as ed

    days = [_date(2026, 1, 1) + _td(days=i) for i in range(250)]
    rising = _pd.Series([100.0 + i for i in range(250)], index=days)
    p = ed.Panels(_pd.DataFrame(), _pd.DataFrame(), _pd.DataFrame(), {}, _pd.DataFrame(), rising)
    assert ed.market_state(p, days[-1]) == "above"
    assert ed.market_state(p, days[100]) is None  # fewer than 200 sessions
    falling = ed.Panels(p.open, p.close, p.volume, {}, p.events,
                        _pd.Series([400.0 - i for i in range(250)], index=days))  # fmt: skip
    assert ed.market_state(falling, days[-1]) == "below"
    line = ed._state_line([{"nifty_vs_200dma": "below"}])
    assert "below its 200-day average" in line and "+0.4% after costs" in line
    assert ed._state_line([{}]) == ""
    assert ed.HISTORY["mean_excess"] < 0.01  # the corrected, after-cost figure


def _with_highs(gap_open: float | None) -> tuple[ed.Panels, list[date]]:
    p = _panels()
    extra, d = [], p.sessions[-1] + timedelta(days=1)
    while len(extra) < 20:
        if d.weekday() < 5:
            extra.append(d)
        d += timedelta(days=1)
    close = pd.concat([p.close, pd.DataFrame(500.0, index=extra, columns=p.close.columns)])
    close.loc[extra, "NSE_J"] = 560.0
    opens, high = close.copy(), close.copy()
    high.loc[extra[5], "NSE_J"] = 620.0  # the +10% target (616) is reached on day 6
    if gap_open is not None:
        opens.loc[extra[5], "NSE_J"] = gap_open
    volume = pd.concat([p.volume, p.volume.iloc[-20:].set_axis(extra)])
    return ed.Panels(opens, close, volume, p.symbols, p.events, None, high), extra


def test_a_call_is_sold_when_it_first_reaches_the_10_percent_target(tmp_path: Path) -> None:
    for gap_open, expected in ((None, 616.0), (630.0, 630.0)):
        p, extra = _with_highs(gap_open)
        ledger = tmp_path / f"calls{gap_open}.jsonl"
        ed._append([{"event": "call", "call_id": "JUMPY:x", "at": "t", **ed.candidates(
            ed.Panels(p.open.iloc[:30], p.close.iloc[:30], p.volume.iloc[:30], p.symbols, p.events)
        )[0]}], ledger)  # fmt: skip
        exited = next(m for m in ed.advance(p, ed.read_ledger(ledger), datetime.now(IST))
                      if m["event"] == "exited")  # fmt: skip
        assert exited["exit_on"] == extra[5].isoformat() and exited["exit_reason"] == "+10% target"
        assert exited["exit_price"] == pytest.approx(expected)  # a gap fills at the open


def test_the_call_message_says_where_to_sell() -> None:
    result = {"issued": [{"symbol": "TECHM", "reference_close": 1790.0, "jump_vs_market": 0.144,
                          "volume_x": 8.1}], "exit_tomorrow": [], "exited": [],
              "verdict": {"status": "live"}}  # fmt: skip
    body = ed.messages(result)[0][1]
    assert "sell at +10% (~Rs 1,969.00) or the close of the 20th session" in body
    assert "place a sell order (GTT) at 1.10x your buy price" in body


def _closed(i: int, net: float) -> list[dict]:
    cid = f"S{i}:d"
    return [
        {"event": "call", "call_id": cid, "at": "t", "symbol": f"S{i}", "scrip_code": f"C{i}"},
        {"event": "entered", "call_id": cid, "at": "t", "entry_price": 100.0, "qty": 100},
        {"event": "exited", "call_id": cid, "at": "t", "exit_on": f"2026-11-{1 + i % 28:02d}",
         "exit_price": 100 * (1 + net), "net_return": net, "excess_return": net,
         "net_inr": 10_000 * net},
    ]  # fmt: skip


def test_the_scorecard_compares_the_paper_record_with_what_is_normal() -> None:
    techm = {"call_id": "TECHM:d", "at": "t", "symbol": "TECHM", "scrip_code": "NSE_T"}
    wipro = {"call_id": "WIPRO:d", "at": "t", "symbol": "WIPRO", "scrip_code": "NSE_W"}
    open_call = [
        {"event": "call", **techm},
        {"event": "entered", "call_id": "TECHM:d", "at": "t", "entry_price": 1790.0, "qty": 5},
        {"event": "call", **wipro},
    ]
    early = ed.scorecard(open_call + _closed(1, -0.05), {"NSE_T": 1850.0}, as_of="x")
    assert early["calls"] == 3 and early["waiting_entry"] == 1 and early["open"] == 1
    assert early["open_mean_net"] == pytest.approx(1850 / 1790 - 1 - 0.00564)
    assert early["status"] == "too early to judge"  # one losing call says nothing

    # realistic swings: half the calls +10%, half -8% (average +1%, about history's spread)
    normal = [row for i in range(30) for row in _closed(i, 0.10 if i % 2 else -0.08)]
    card = ed.scorecard(normal, {}, as_of="x")
    low, high = card["normal_range_80"]
    assert low < card["closed_mean_net"] < high and card["status"] == "in line with history"
    assert card["closed_calls_for_proof"] > 50 and not card["proven"]

    bad = [row for i in range(30) for row in _closed(i, -0.05)]
    assert ed.scorecard(bad, {}, as_of="x")["status"] == "worse than history - watch closely"

    title, body = ed.scorecard_message(card)
    assert title == "Earnings-drift paper record (as if Rs 10,000 each)"
    assert "Closed: 15 of 30 won" in body and "Normal for 30 closed calls" in body
    assert "losing runs are normal" in body and "market down day" not in body  # none marked
    assert ed.scorecard_message({}) is None


def test_the_paper_record_reports_down_day_calls_apart() -> None:
    rows = [
        {"event": "call", "call_id": "A:d", "at": "t", "symbol": "A", "scrip_code": "NSE_A",
         "down_day": True},
        {"event": "entered", "call_id": "A:d", "at": "t", "entry_price": 100.0, "qty": 100},
        *_closed(1, 0.04),
    ]  # fmt: skip
    rows[2] = {**rows[2], "down_day": False}
    card = ed.scorecard(rows, {"NSE_A": 95.0}, as_of="x")
    split = card["by_market_day"]
    assert split["down_day"]["open"] == 1 and split["down_day"]["closed"] == 0
    assert split["other_days"] == {"open": 0, "open_mean_net": None, "closed": 1,
                                   "closed_mean_net": 0.04}  # fmt: skip
    body = ed.scorecard_message(card)[1]
    assert "Made on a market down day: 1 open -5.6%; other days: 1 closed +4.0%" in body
    assert "200-day average" not in body  # no call carries the trend mark here


def test_the_paper_record_reports_calls_above_and_below_nifty_200dma_apart() -> None:
    rows = [
        {"event": "call", "call_id": "A:d", "at": "t", "symbol": "A", "scrip_code": "NSE_A",
         "nifty_vs_200dma": "below"},
        {"event": "entered", "call_id": "A:d", "at": "t", "entry_price": 100.0, "qty": 100},
        *_closed(1, 0.04),
    ]  # fmt: skip
    rows[2] = {**rows[2], "nifty_vs_200dma": "above"}
    card = ed.scorecard(rows, {"NSE_A": 95.0}, as_of="x")
    trend = card["by_nifty_trend"]
    assert trend["below_200dma"]["open"] == 1 and trend["above_200dma"]["closed"] == 1
    body = ed.scorecard_message(card)[1]
    assert "NIFTY above its 200-day average: 1 closed +4.0%; below: 1 open -5.6%" in body
