"""nifty_swing: the index is the one of the day, fills are honest and no signal sees the future."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import nifty_swing as ns


def _days(n: int, first: date = date(2021, 1, 4)) -> list[date]:
    out, day = [], first
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _bars(rows: list[tuple[float, float, float, float]], symbol: str = "AAA") -> ns.Bars:
    days = _days(len(rows))
    frame = {k: pd.DataFrame({symbol: [r[i] for r in rows]}, index=days)
             for i, k in enumerate(("open", "high", "low", "close"))}  # fmt: skip
    volume = pd.DataFrame({symbol: [1000.0] * len(rows)}, index=days)
    nifty = pd.Series(np.linspace(100, 200, len(rows)), index=days)
    return ns.Bars(frame["open"], frame["high"], frame["low"], frame["close"], volume, nifty)


def _one(rows: list[tuple[float, float, float, float]], atr: float = 2.0) -> dict[str, float]:
    """The trade signalled on the first bar: close 100 and ATR 2 put the stop at 97."""

    bars = _bars(rows)
    atr_frame = pd.DataFrame({"AAA": [atr] * len(rows)}, index=bars.close.index)
    out = ns.outcomes(bars, atr_frame, lambda qty, entry, exit_px, same_day: 0.0)
    return {"net_r": float(out.net_r[0, 0]), "net_inr": float(out.net_inr[0, 0]),
            "qty": float(out.qty[0, 0]), "exit_row": int(out.exit_row[0, 0]),
            "entry": float(out.entry[0, 0])}  # fmt: skip


FLAT = (100.0, 101.0, 99.5, 100.0)
ENTRY = 100.0 * (1 + ns.SLIPPAGE)
RISK = ENTRY - 97.0


def test_the_index_chains_back_to_the_51_securities_of_2016() -> None:
    first = ns.initial_members()
    assert len(first) == 51
    assert {"TATAMTRDVR", "HDFC", "TMPV", "INDUSTOWER", "IDEA"} <= first
    assert not {"BAJFINANCE", "TITAN", "BSE"} & first


def test_membership_is_the_index_of_the_day() -> None:
    days = [date(2017, 9, 28), date(2017, 9, 29), date(2023, 7, 12), date(2023, 7, 13),
            date(2026, 9, 29), date(2026, 9, 30)]  # fmt: skip
    members = ns.membership(days, ["WIPRO", "BSE", "LTM"])
    assert list(members.sum(axis=1)) == [51, 50, 50, 50, 50, 50]
    assert list(members["HDFC"]) == [True, True, True, False, False, False]
    assert list(members["LTM"]) == [False, False, False, True, False, False]
    assert list(members["WIPRO"]) == [True, True, True, True, True, False]
    assert list(members["BSE"]) == [False, False, False, False, False, True]
    assert set(members.columns[members.iloc[-1]]) == set(ns.CURRENT)


def test_an_unadjusted_split_is_scaled_out_and_listed() -> None:
    rows = [(100, 101, 99, 100), (100, 102, 99, 101), (50.5, 51, 50, 50.6), (50.6, 51, 50, 50.7)]
    bars = _bars(rows)
    scaled, found = ns.adjust(bars)
    assert [f["day"] for f in found] == [str(bars.close.index[2])]
    assert scaled.close["AAA"].iloc[1] == pytest.approx(50.5)
    assert scaled.close["AAA"].iloc[2] == pytest.approx(50.6)
    assert scaled.volume["AAA"].iloc[0] == pytest.approx(2000.0)
    assert ns.adjust(_bars([FLAT, (110, 111, 109, 110), FLAT]))[1] == []


def test_a_stop_costs_about_one_r() -> None:
    got = _one([FLAT, (100, 101, 96, 98), *[FLAT] * 10])
    assert got["qty"] == np.floor(ns.RISK_INR / RISK)
    assert got["net_r"] == pytest.approx((97 * (1 - ns.SLIPPAGE) - ENTRY) / RISK)
    assert got["exit_row"] == 1


def test_a_gap_through_the_stop_fills_at_the_open() -> None:
    got = _one([FLAT, FLAT, (95, 96, 94, 95), *[FLAT] * 10])
    assert got["net_r"] == pytest.approx((95 * (1 - ns.SLIPPAGE) - ENTRY) / RISK)
    assert got["net_r"] < -1.5


def test_a_bar_touching_stop_and_target_is_a_stop() -> None:
    got = _one([FLAT, (100, 120, 96, 110), *[FLAT] * 10])
    assert got["net_r"] < -1.0


def test_after_target_one_the_rest_cannot_lose() -> None:
    t1 = ENTRY + ns.T1_R * RISK
    got = _one([FLAT, FLAT, (101, t1 + 0.1, 100.5, 104), (104, 104, 99, 99.5), *[FLAT] * 10])
    qty = got["qty"]
    first = np.ceil(qty / 2)
    rest = (qty - first) * (ENTRY * (1 - ns.SLIPPAGE) - ENTRY)
    assert got["net_inr"] == pytest.approx(first * (t1 - ENTRY) + rest)
    assert got["exit_row"] == 3


def test_both_targets_pay_two_and_three_r() -> None:
    t2 = ENTRY + ns.T2_R * RISK
    got = _one([FLAT, FLAT, (101, t2 + 1, 100.5, t2), *[FLAT] * 10])
    qty = got["qty"]
    first = np.ceil(qty / 2)
    assert got["net_inr"] == pytest.approx(RISK * (2 * first + 3 * (qty - first)))


def test_an_open_above_a_target_fills_at_the_open() -> None:
    t2 = ENTRY + ns.T2_R * RISK
    got = _one([FLAT, FLAT, (t2 + 5, t2 + 6, t2 + 4, t2 + 5), *[FLAT] * 10])
    assert got["net_inr"] == pytest.approx(got["qty"] * (t2 + 5 - ENTRY))


def test_an_unfinished_trade_leaves_at_the_tenth_close() -> None:
    rows = [FLAT] * 10 + [(100, 103, 99.5, 102.0), FLAT, FLAT]
    got = _one(rows)
    assert got["exit_row"] == ns.MAX_HOLD
    assert got["net_r"] == pytest.approx((102 * (1 - ns.SLIPPAGE) - ENTRY) / RISK)


def test_no_trade_when_chased_dead_or_unfinished() -> None:
    assert np.isnan(_one([FLAT, (102.5, 103, 102, 102.5), *[FLAT] * 10])["net_r"])  # > 1 ATR up
    assert np.isnan(_one([FLAT, (96.5, 97, 96, 96.5), *[FLAT] * 10])["net_r"])  # under the stop
    assert np.isnan(_one([FLAT] * 6)["net_r"])  # the history ends inside the trade


def test_a_dear_stock_is_capped_at_a_third_of_the_capital() -> None:
    dear = tuple(v * 100 for v in FLAT)
    got = _one([dear] * 13, atr=20.0)  # risk a share ~Rs 35: Rs 500 would buy 14 shares
    assert got["qty"] == np.floor(ns.MAX_POSITION_INR / (10_000 * (1 + ns.SLIPPAGE)))


def _random_bars(rows: int = 420, symbols: int = 6, seed: int = 7) -> ns.Bars:
    rng = np.random.default_rng(seed)
    days = _days(rows, date(2018, 1, 1))
    names = [f"S{i}" for i in range(symbols)]
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.018, (rows, symbols)), axis=0))
    open_ = np.vstack([close[:1], close[:-1]]) * (1 + rng.normal(0, 0.004, (rows, symbols)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, (rows, symbols))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, (rows, symbols))))
    volume = rng.lognormal(10, 0.6, (rows, symbols))

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=days, columns=names)

    nifty = pd.Series(100 * np.exp(np.cumsum(np.full(rows, 0.0008))), index=days)
    return ns.Bars(frame(open_), frame(high), frame(low), frame(close), frame(volume), nifty)


def _cut(bars: ns.Bars, rows: int) -> ns.Bars:
    return ns.Bars(*(getattr(bars, k).iloc[:rows] for k in
                     ("open", "high", "low", "close", "volume", "nifty")))  # fmt: skip


@pytest.mark.parametrize("name", list(ns.SETTINGS))
def test_no_signal_changes_when_later_bars_are_removed(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ns, "WARM_UP", 210)
    full, keep = _random_bars(), 380
    part = _cut(full, keep)
    everyone = pd.DataFrame(True, index=full.close.index, columns=full.close.columns)
    setting = ns.SETTINGS[name][1]
    whole = ns.signals(name, full, ns.features(full), everyone, setting)
    early = ns.signals(name, part, ns.features(part), everyone.iloc[:keep], setting)
    assert np.array_equal(whole[:keep], early)


def test_the_strategies_do_fire_on_ordinary_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ns, "WARM_UP", 210)
    bars = _random_bars(rows=700, symbols=12)
    f = ns.features(bars)
    everyone = pd.DataFrame(True, index=bars.close.index, columns=bars.close.columns)
    for name, (_, setting, _) in ns.SETTINGS.items():
        assert ns.signals(name, bars, f, everyone, setting).sum() > 0, name


def test_a_stock_outside_the_index_never_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ns, "WARM_UP", 210)
    bars = _random_bars(rows=700, symbols=4)
    f = ns.features(bars)
    members = pd.DataFrame(True, index=bars.close.index, columns=bars.close.columns)
    members["S0"] = False
    for name, (_, setting, _) in ns.SETTINGS.items():
        assert ns.signals(name, bars, f, members, setting)[:, 0].sum() == 0


def test_a_stock_waits_five_sessions_before_signalling_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ns, "WARM_UP", 0)
    bars = _random_bars(rows=60, symbols=1)
    always = pd.DataFrame(True, index=bars.close.index, columns=bars.close.columns)
    monkeypatch.setattr(ns, "raw_signal", lambda *a, **k: always)
    got = ns.signals("momentum", bars, ns.features(bars), always, 25)[:, 0]
    assert got.sum() > 3
    assert np.diff(np.nonzero(got)[0]).min() == ns.COOL_DOWN + 1


def _trades(rows: list[tuple[int, int, str, float]], strength: float = 0.1) -> pd.DataFrame:
    days = _days(60)
    return pd.DataFrame({
        "entry_row": [r[0] for r in rows], "exit_row": [r[1] for r in rows],
        "day": [days[r[0]] for r in rows], "symbol": [r[2] for r in rows],
        "net_inr": [r[3] for r in rows], "net_r": [r[3] / ns.RISK_INR for r in rows],
        "strength": [strength] * len(rows),
    })  # fmt: skip


def test_the_book_holds_three_positions_and_one_per_stock() -> None:
    trades = _trades([(1, 9, "A", 100), (1, 9, "B", 100), (2, 9, "A", 100), (2, 9, "C", 100),
                      (3, 9, "D", 100), (9, 12, "E", 100), (10, 12, "F", 100)])  # fmt: skip
    held = ns.book(trades, _days(60))
    assert sorted(held["symbol"]) == ["A", "B", "C", "F"]  # E arrives on the exit session


def test_the_book_takes_the_strongest_first() -> None:
    trades = _trades([(1, 9, s, 100) for s in "ABCD"])
    trades["strength"] = [0.1, 0.4, 0.3, 0.2]
    assert sorted(ns.book(trades, _days(60))["symbol"]) == ["B", "C", "D"]


def test_no_new_entry_in_a_week_that_has_lost_two_percent() -> None:
    # rows 0-4 are one Monday-to-Friday week of 2021
    trades = _trades([(0, 1, "A", -1200), (0, 1, "B", -900), (3, 4, "C", 100), (5, 6, "D", 100)])
    assert sorted(ns.book(trades, _days(60))["symbol"]) == ["A", "B", "D"]


def test_the_fall_is_measured_from_the_running_peak() -> None:
    assert ns.worst_fall([100, -300, 50, -100, 600]) == 350
    assert ns.worst_fall([]) == 0
    assert ns.losing_streak([1, -1, 0, -2, 3, -1]) == 3


def test_any_day_excess_counts_one_week_once() -> None:
    days = _days(15)
    base = pd.DataFrame({"day": days, "net_r": [0.1] * 15})
    mine = pd.DataFrame({"day": [days[0], days[1], days[7], days[12]],
                         "net_r": [0.5, 0.3, 0.2, 0.6]})  # fmt: skip
    got = ns.vs_any_day(mine, base)
    assert got["weeks"] == 3
    assert got["mean"] == pytest.approx((0.3 + 0.1 + 0.5) / 3)


def _judged(net_r: list[float], years: list[int], **held: float) -> dict:
    trades = pd.DataFrame({"day": [date(y, 6, 1) for y in years], "net_r": net_r,
                           "net_inr": [r * 500 for r in net_r]})  # fmt: skip
    edge = {"mean": 0.2, "t": 3.5, "in_sample": 0.1, "out_of_sample": 0.1}
    return ns.judge(trades, {"worst_fall": 0.05, "worst_fall_mc95": 0.08, **held}, edge, [0.1, 0.1])


def test_a_strategy_meeting_every_part_is_a_proven_edge() -> None:
    years = [2019] * 30 + [2020] * 30 + [2023] * 30 + [2024] * 30
    got = _judged([0.3] * 120, years)
    assert got["approved_for_paper"] and got["proven_edge"], got["fails"]


def test_each_failing_part_is_named() -> None:
    years = [2019] * 30 + [2020] * 30 + [2023] * 30 + [2024] * 30
    lost_late = _judged([0.3] * 60 + [-0.1] * 60, years)
    assert "loses out of sample" in lost_late["fails"]
    assert not lost_late["proven_edge"]
    assert "too few trades" in _judged([0.3] * 50, [2019] * 25 + [2023] * 25)["fails"][0]
    assert "book falls more than 10%" in _judged([0.3] * 120, years, worst_fall=0.12)["fails"]
    trades = pd.DataFrame({"day": [date(y, 6, 1) for y in years], "net_r": [0.3] * 120,
                           "net_inr": [150.0] * 120})  # fmt: skip
    weak = {"mean": 0.2, "t": 1.4, "in_sample": 0.1, "out_of_sample": 0.1}
    got = ns.judge(trades, {"worst_fall": 0.05, "worst_fall_mc95": 0.08}, weak, [0.1, -0.2])
    assert "a neighbouring setting loses" in got["fails"]
    assert got["edge_fails"] == ["t 1.40 over any-day entries"]
