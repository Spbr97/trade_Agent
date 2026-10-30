"""Option-income paper test: fills on the side a real order hits, per-leg stops, the 15:15
square-off, the condor's capped loss at expiry and statutory costs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tradedesk_lab import option_income as oi


def _leg(bid: float, ask: float, delta: float = 0.5) -> dict:
    return {"top_bid_price": bid, "top_ask_price": ask, "last_price": (bid + ask) / 2,
            "iv": 12.0, "greeks": {"delta": delta}}  # fmt: skip


def _chain(ltp: float, legs: dict[float, tuple[tuple, tuple]]) -> dict:
    return {
        "underlying_ltp": ltp, "expiry": "2026-10-27",
        "strikes": {f"{k:.1f}": {"ce": _leg(*ce), "pe": _leg(*pe)} for k, (ce, pe) in legs.items()},
    }  # fmt: skip


COSTS = oi.OptionCosts()


def test_straddle_sells_at_the_bid_and_stops_each_leg_on_the_ask() -> None:
    open_chain = _chain(23140, {23100: ((99, 100), (79, 80)), 23150: ((70, 71), (100, 101))})
    s30, s40 = oi.open_straddles(open_chain, "09:20")
    assert s30.legs[0].strike == 23150 and s30.legs[0].entry == 70 and s30.legs[1].entry == 100
    assert s30.legs[0].stop == pytest.approx(91.0) and s40.legs[0].stop == pytest.approx(98.0)

    rally = _chain(23300, {23150: ((92, 93), (40, 41))})  # call ask 93 >= 91: the 30% stop
    for s in (s30, s40):
        s.step(rally, "11:00", square_off=False)
    assert s30.legs[0].exit == 93 and s30.legs[0].reason == "stop" and s30.legs[1].exit is None
    assert s40.legs[0].exit is None  # 93 < 98

    close = _chain(23290, {23150: ((88, 89), (20, 21))})
    for s in (s30, s40):
        s.step(close, "15:15", square_off=True)
    assert s30.done and s40.done
    r30 = s30.result(COSTS)
    assert r30["gross_inr"] == pytest.approx(((70 - 93) + (100 - 21)) * oi.LOT)
    assert r30["net_inr"] == pytest.approx(r30["gross_inr"] - r30["costs_inr"])
    assert s40.result(COSTS)["gross_inr"] == pytest.approx(((70 - 89) + (100 - 21)) * oi.LOT)


def test_costs_follow_the_statutory_rates() -> None:
    sell = COSTS.trade(100.0, 65, "sell")  # premium value Rs 6,500
    exchange, sebi = 6500 * 0.0003503, 6500 * 0.000001
    expected = 20 + exchange + sebi + 0.18 * (20 + exchange + sebi) + 6500 * 0.001
    assert sell == pytest.approx(round(expected, 2))
    assert COSTS.trade(100.0, 65, "buy") < sell  # stamp 0.003% instead of STT 0.1%


def test_condor_takes_the_20_delta_shorts_and_caps_the_loss() -> None:
    chain = _chain(23000, {
        22600: ((5, 6, -0.10), (30, 31, -0.10)), 22800: ((60, 61, 0.7), (40, 41, -0.21)),
        23000: ((90, 91, 0.5), (90, 91, -0.5)), 23200: ((42, 43, 0.19), (60, 61, -0.7)),
        23400: ((12, 13, 0.08), (5, 6, -0.9)),
    })  # fmt: skip
    condor = oi.open_condor(chain, "09:30")
    assert condor is not None
    legs = condor["legs"]
    assert legs["short_ce"]["strike"] == 23200 and legs["long_ce"]["strike"] == 23400
    assert legs["short_pe"]["strike"] == 22800 and legs["long_pe"]["strike"] == 22600
    assert condor["credit"] == pytest.approx(42 + 40 - 13 - 31)

    inside = oi.settle_condor(condor, 23050, COSTS)
    assert inside["gross_inr"] == pytest.approx(condor["credit"] * oi.LOT)
    crash = oi.settle_condor(condor, 21000, COSTS)  # a 9% fall: the put wing caps it
    assert crash["gross_inr"] == pytest.approx(-(200 - condor["credit"]) * oi.LOT)
    assert crash["gross_inr"] == pytest.approx(-condor["max_loss_inr"])


def test_summary_reports_totals_per_rule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(oi, "CONDOR_STATE", tmp_path / "none.json")
    ledger = tmp_path / "ledger.jsonl"
    for net in (1200.0, -800.0):
        oi.append(ledger, {"rule": "straddle_30", "net_inr": net, "closed_on": "2026-10-19"})
    title, body = oi.summary(ledger, today=__import__("datetime").date(2026, 10, 19))
    assert "straddle_30: today -800, 2 closed, total Rs +400" in body and "PAPER ONLY" in body
    assert json.loads(ledger.read_text().splitlines()[0])["net_inr"] == 1200.0


def test_straddle_records_its_worst_point_and_the_whipsaw_day() -> None:
    open_chain = _chain(23140, {23150: ((70, 71), (100, 101))})
    s30, _ = oi.open_straddles(open_chain, "09:20")
    s30.step(_chain(23250, {23150: ((88, 89), (94, 95))}), "10:00", square_off=False)
    s30.step(_chain(23150, {23150: ((50, 51), (60, 61))}), "15:15", square_off=True)
    r = s30.result(COSTS)
    assert r["worst_open_inr"] == pytest.approx(((70 - 89) + (100 - 95)) * oi.LOT)
    gross = -(0.30 * 70 + 0.30 * 100) * oi.LOT
    assert gross - 600 < r["both_stopped_inr"] < gross  # the stops' loss plus four orders' costs


def test_stress_replays_old_trades_from_saved_chains(tmp_path: Path) -> None:
    legs = [
        {"side": "ce", "strike": 23150.0, "entry": 70.0, "stop": 91.0, "exit": 95.0,
         "exit_at": "T11", "reason": "stop"},
        {"side": "pe", "strike": 23150.0, "entry": 100.0, "stop": 130.0, "exit": 130.5,
         "exit_at": "T14", "reason": "stop"},
    ]  # fmt: skip
    row = {"rule": "straddle_30", "expiry": "E", "opened_at": "T09", "net_inr": -3000.0,
           "closed_on": "2026-10-20", "legs": legs}  # fmt: skip
    win = {**row, "net_inr": 1000.0, "legs": [{**x, "exit": 50.0, "reason": "15:15"} for x in legs]}
    minutes = [
        {"t": "T10", "expiry": "E", "strikes": {"23150.0": {"ce": [89, 90, 0, 0, 0],
                                                           "pe": [99, 100, 0, 0, 0]}}},
        {"t": "T12", "expiry": "E", "strikes": {"23150.0": {"ce": [9, 10, 0, 0, 0],
                                                           "pe": [119, 120, 0, 0, 0]}}},
    ]  # fmt: skip
    (tmp_path / "2026-10-20.jsonl").write_text("\n".join(json.dumps(m) for m in minutes))
    st = oi.stress([row, win], COSTS, chain_dir=tmp_path)["straddle_30"]
    assert st["worst_open_inr"] == pytest.approx(((70 - 95) + (100 - 120)) * oi.LOT)
    assert st["both_stopped_days"] == 1 and st["stops"] == 2
    assert st["stop_fill_overshoot"] == pytest.approx((95 / 91 - 1 + 130.5 / 130 - 1) / 2)
    assert st["both_stopped_in_wins"] > 1
