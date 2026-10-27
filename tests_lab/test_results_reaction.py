from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from tradedesk_lab import results_reaction as rr

from tradedesk.broker.indstocks.models import IST

DAYS = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)]


def _bars(spikes: dict[tuple[str, int], tuple[int, float, float]]) -> pd.DataFrame:
    """Four flat stocks at Rs 100, volume 1000 a bar. spikes[(code, day_index)] =
    (bar, close from that bar on, volume of that bar)."""
    rows = []
    for code in ("A", "B", "C", "D"):
        for i, d in enumerate(DAYS):
            spike = spikes.get((code, i))
            for bar in range(25):
                start = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
                t = start + timedelta(minutes=15 * bar)
                close, vol = 100.0, 1000.0
                if spike and bar >= spike[0]:
                    close = spike[1]
                    vol = spike[2] if bar == spike[0] else 1000.0
                rows.append({"code": code, "ts": int(t.timestamp()), "open": close, "high": close,
                             "low": close, "close": close, "volume": vol})  # fmt: skip
    return pd.DataFrame(rows)


def test_usual_volume_uses_only_earlier_sessions() -> None:
    df = rr.prepare(_bars({("A", 25): (3, 105.0, 9000.0)}))
    row = df[(df.scrip_code == "A") & (df.day == DAYS[25]) & (df.bar == 3)].iloc[0]
    assert row.typ_vol == 1000.0 and row.vol_x == 9.0
    later = df[(df.scrip_code == "A") & (df.day == DAYS[26]) & (df.bar == 3)].iloc[0]
    assert later.typ_vol == 1000.0  # a median, so one spike does not move it


def test_watch_days_are_the_results_session_and_the_next() -> None:
    assert rr.watch_days(DAYS, DAYS[3]) == [DAYS[3], DAYS[4]]
    assert rr.watch_days(DAYS, DAYS[3] - timedelta(hours=0)) == [DAYS[3], DAYS[4]]
    assert rr.watch_days(DAYS[:4], DAYS[3]) == [DAYS[3]]


def test_a_results_pop_is_news_and_the_same_pop_without_results_is_control() -> None:
    # B's +9% at bar 5 is +5.25% over a four-stock market that A and B lift by 3.75%.
    df = rr.prepare(_bars({("A", 26): (3, 106.0, 8000.0), ("B", 26): (5, 109.0, 8000.0)}))
    events = {"A": [DAYS[25]]}  # results after the close on day 25: the pop comes on day 26
    news = rr.news_signals(df, events, 0.04)
    assert list(zip(news.scrip_code, news.day, news.bar, strict=True)) == [("A", DAYS[26], 3)]
    control = rr.control_signals(df, events, set(news.day), 0.04)
    assert list(control.scrip_code) == ["B"]
    assert rr.news_signals(df, events, 0.07).empty  # 6% move, 4 stocks' market: not enough


def test_intraday_outcome_is_net_of_costs_and_the_market() -> None:
    spikes = {("A", 20): (3, 106.0, 8000.0)}
    df = rr.prepare(_bars(spikes))
    ctx = rr.context(df, lambda kind, entry, exit_px: 0.001)
    sig = rr.news_signals(df, {"A": [DAYS[20]]}, 0.04).iloc[0].to_dict()
    out = rr.outcome(sig, ctx, "same_day")
    entry, exit_px = 106.0 * (1 + rr.SLIPPAGE), 106.0 * (1 - rr.SLIPPAGE)
    assert abs(out["net"] - (exit_px / entry - 1 - 0.001)) < 1e-12
    assert abs(out["excess"] - out["net"]) < 1e-12  # the market did not move after the bar
    held = rr.outcome(sig, ctx, "5_sessions")
    assert held is not None and held["net"] < 0  # flat after the pop: costs only


def test_the_fade_shorts_the_pop_and_gains_when_it_gives_it_back() -> None:
    spikes = {("A", 20): (3, 106.0, 8000.0)}
    bars = _bars(spikes)
    late = (bars.code == "A") & (bars.ts >= bars.ts.min())  # A falls back to 103 from bar 10
    day20 = [int(datetime(DAYS[20].year, DAYS[20].month, DAYS[20].day, 9, 15, tzinfo=IST)
                 .timestamp()) + 900 * b for b in range(10, 25)]  # fmt: skip
    late &= bars.ts.isin(day20)
    bars.loc[late, ["open", "high", "low", "close"]] = 103.0
    df = rr.prepare(bars)
    ctx = rr.context(df, lambda kind, buy, sell: 0.001)
    sig = rr.news_signals(df, {"A": [DAYS[20]]}, 0.02).iloc[0].to_dict()
    out = rr.fade_outcome(sig, ctx)
    entry, cover = 106.0 * (1 - rr.SLIPPAGE), 103.0 * (1 + rr.SLIPPAGE)
    assert abs(out["net"] - ((entry - cover) / entry - 0.001)) < 1e-12
    assert out["net"] > 0 and out["mae"] < 0.001  # never traded above the short price


def test_the_fade_verdict_waits_for_enough_forward_days() -> None:
    rows = [{"day": DAYS[i % 10], "net": 0.004, "excess": 0.004 + 0.001 * (i % 3), "mae": 0.01}
            for i in range(200)]  # fmt: skip
    assert rr.fade_verdict(rows)["status"] == "collecting"  # 10 days < 20
    rows = [{**r, "day": DAYS[i % 25]} for i, r in enumerate(rows)]
    assert rr.fade_verdict(rows)["status"] == "passed"
    losing = [{**r, "net": -0.002, "excess": -0.002} for r in rows]
    assert rr.fade_verdict(losing)["status"] == "failed"
    assert rr.fade_verdict([])["status"] == "collecting"


def test_the_pass_bar() -> None:
    days = [DAYS[i] for i in range(20)]
    good = pd.DataFrame({"day": days * 6, "net": [0.01, 0.012, 0.008] * 40,
                         "excess": [0.01, 0.012, 0.008] * 40})  # fmt: skip
    flat = good.assign(excess=0.0, net=0.0)
    assert rr.judge(good, flat)["passed"]
    verdict = rr.judge(good.head(50), flat)
    assert not verdict["passed"] and "only 50 trades" in verdict["fails"]
    assert not rr.judge(flat, flat)["passed"]
