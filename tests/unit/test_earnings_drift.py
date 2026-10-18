from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

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
