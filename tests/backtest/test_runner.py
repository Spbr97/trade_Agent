"""End-to-end backtests on an engineered universe: gap-fill, crowded-day, look-ahead and
walk-forward (PLAN.md M5 'done when')."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from tests.backtest.synth_universe import benchmark, breakout_stock
from tests.data.synth import sessions
from tradedesk.backtest.reports import build_report, walk_forward
from tradedesk.backtest.runner import BacktestConfig, prepare_market, run_backtest
from tradedesk.broker.indstocks.models import IST, IndexInstrument, Interval
from tradedesk.config.models import EngineConfig, RiskConfig
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.universe import UniverseRules
from tradedesk.engine.engine import MarketSnapshot, scan_day
from tradedesk.engine.lifecycle import SignalState
from tradedesk.engine.signals import SetupKind

NIFTY = IndexInstrument(exch="NSE", name="NIFTY 50", security_id="40000001")
REF = NIFTY.scrip_code
CAL = sessions(date(2024, 1, 1), 330)
BREAKOUT = 300  # session index of the breakout day
PARAMS = {
    "base_breakout": {"rs_percentile_min": 0, "max_volume_dryup": 1.0, "max_stop_distance_atr": 3.0}
}
RULES = UniverseRules(min_avg_turnover_inr=1.0, min_price=1.0)


def config(start: date, end: date, capital: float = 1_000_000.0) -> BacktestConfig:
    return BacktestConfig(
        setups=[SetupKind.BASE_BREAKOUT],
        start=start,
        end=end,
        capital=capital,
        risk=RiskConfig(trading_capital=Decimal(str(capital))),
        engine=EngineConfig(),
        setup_params=PARAMS,
        universe_rules=RULES,
        slippage_pct=0.0,
        warmup_sessions=280,
    )


def build_store(stocks: dict[str, tuple[str, int]]) -> CandleStore:
    """stocks: code -> (aftermath, seed). All break out on the same session."""
    store = CandleStore()
    store.upsert_instruments([NIFTY])
    store.upsert_candles(benchmark(REF, CAL))
    for code, (aftermath, seed) in stocks.items():
        store.upsert_candles(
            breakout_stock(code, CAL, breakout_index=BREAKOUT, aftermath=aftermath, seed=seed)
        )
    return store


def run(stocks: dict[str, tuple[str, int]], **kw: object):  # type: ignore[no-untyped-def]
    store = build_store(stocks)
    cfg = config(CAL[BREAKOUT - 40], CAL[-1], **kw)  # type: ignore[arg-type]
    md = prepare_market(store, list(stocks), REF, cfg)
    return store, cfg, md, run_backtest(md, cfg)


def test_winner_partial_then_trail_and_gap_loser() -> None:
    store, cfg, md, res = run({"NSE_WIN": ("win", 1), "NSE_GAP": ("gap", 2)})
    # The random advance can form small bases of its own; judge the engineered breakout day.
    trades = {t.scrip_code: t for t in res.portfolio.closed if t.entry_date == CAL[BREAKOUT]}
    assert set(trades) == {"NSE_WIN", "NSE_GAP"}

    win = trades["NSE_WIN"]
    reasons = [f.reason.value for f in win.position.fills]
    assert "partial" in reasons and win.r_multiple > 1.0
    assert win.exit_reason in {"trail", "max_hold", "time_stop"}
    assert win.entry_date == CAL[BREAKOUT]

    gap = trades["NSE_GAP"]
    assert gap.exit_reason == "gap_stop" and gap.exit_date == CAL[BREAKOUT + 1]
    assert gap.r_multiple < -1.0 and gap.gap_damage > 0  # filled at the open, below the stop
    exit_fill = gap.position.fills[-1]
    assert exit_fill.price < gap.position.signal.stop

    states = {ts.signal.scrip_code: ts.state for ts in res.signals}
    assert states["NSE_WIN"] is SignalState.CLOSED and states["NSE_GAP"] is SignalState.CLOSED
    rep = build_report(res)
    assert rep.overall.trades >= 2 and rep.overall.gap_losses == 1
    assert rep.overall.total_costs > 0
    assert "base_breakout" in rep.by_setup
    assert rep.text()


def test_crowded_day_respects_daily_and_position_caps() -> None:
    stocks = {f"NSE_{i}": ("win", 10 + i) for i in range(8)}
    _, _, _, res = run(stocks)
    entries_by_day: dict[date, int] = {}
    for t in res.portfolio.closed:
        entries_by_day[t.entry_date] = entries_by_day.get(t.entry_date, 0) + 1
    assert 1 <= entries_by_day.get(CAL[BREAKOUT], 0) <= 3  # max 3 new entries per day
    assert max(entries_by_day.values()) <= 3
    peak_open = 0
    held: dict[date, int] = {}
    for t in res.portfolio.closed:
        d = t.entry_date
        while d <= t.exit_date:
            held[d] = held.get(d, 0) + 1
            d += timedelta(days=1)
    peak_open = max(held.values())
    assert peak_open <= 5
    skipped = [ts for ts in res.signals if ts.state is SignalState.SKIPPED]
    assert skipped and all("max" in ts.history[-1].note for ts in skipped)


def test_signals_for_a_date_do_not_depend_on_later_bars() -> None:
    stocks = {"NSE_A": ("win", 3), "NSE_B": ("stop", 4)}
    store, cfg, md, res = run(stocks)
    armed_dates = sorted({ts.signal.armed_on for ts in res.signals})
    assert armed_dates, "the engineered bases must arm at least one signal"
    d = armed_dates[0]
    full = sorted(
        (ts.signal.id, ts.signal.trigger, ts.signal.stop)
        for ts in res.signals
        if ts.signal.armed_on == d
    )
    # Rebuild the world with every bar after `d` deleted and scan that day again.
    cut = CandleStore()
    cut.upsert_instruments([NIFTY])
    cutoff = datetime.combine(d + timedelta(days=1), time.min, tzinfo=IST)
    for code in [REF, *stocks]:
        df = store.load(code, Interval.D1, None, cutoff, adjusted=False)
        from tests.data.synth import daily  # noqa: F401 - keep import local

        cut.con.register("_tmp", df.reset_index().assign(scrip_code=code, interval="1day"))
        cut.con.execute(
            "INSERT OR REPLACE INTO candles SELECT scrip_code, interval, "
            "CAST(epoch(ts) AS BIGINT), open, high, low, close, volume FROM _tmp"
        )
        cut.con.unregister("_tmp")
    md_cut = prepare_market(cut, list(stocks), REF, config(cfg.start, d))
    snapshot = MarketSnapshot(
        on=d,
        features=md_cut.features,
        symbols=md_cut.symbols,
        rs_percentile={
            c: float(md_cut.rs_rank.loc[d][c]) for c in stocks if d in md_cut.rs_rank.index
        },
    )
    again = sorted(
        (s.id, s.trigger, s.stop)
        for s in scan_day(snapshot, cfg.setups, cfg.setup_params, max_hold_sessions=10)
    )
    assert again == full


def test_walk_forward_split_reports() -> None:
    _, _, _, res = run({"NSE_A": ("win", 5), "NSE_B": ("gap", 6)})
    split = CAL[BREAKOUT + 5]
    in_sample, out_sample = walk_forward(res, split)
    assert in_sample.overall.trades >= 2 and out_sample.overall.trades == 0
    assert in_sample.overall.trades + out_sample.overall.trades == build_report(res).overall.trades
    assert in_sample.start_equity == pytest.approx(1_000_000.0)
    assert 0.0 <= in_sample.max_drawdown_pct < 0.05


def test_risk_off_regime_blocks_entries() -> None:
    store = build_store({"NSE_A": ("win", 7)})
    # Crush the benchmark so it sits far below its EMA50 with weak breadth (breadth = this one
    # stock, which is fine, so use VIX spike instead via a VIX series).
    vix_code = "NSE_VIX"
    store.upsert_instruments([IndexInstrument(exch="NSE", name="INDIA VIX", security_id="99")])
    vix = benchmark(vix_code, CAL, start=12.0, seed=1)
    spiked = [
        c.model_copy(update={"close": 30.0, "open": 30.0, "high": 30.5, "low": 29.5})
        if i >= BREAKOUT - 3
        else c
        for i, c in enumerate(vix)
    ]
    store.upsert_candles(spiked)
    cfg = config(CAL[BREAKOUT - 40], CAL[-1])
    cfg.vix_code = vix_code
    md = prepare_market(store, ["NSE_A"], REF, cfg)
    res = run_backtest(md, cfg)
    spike_from = CAL[BREAKOUT - 3]
    assert all(t.entry_date < spike_from for t in res.portfolio.closed)  # nothing after the spike
    assert any(why == "regime risk_off" for _, _, why in res.portfolio.rejections)


# ------------------------------------------------------- duplicate-day bars (M13 Phase 4)


def test_dedupe_by_calendar_day_keeps_the_later_bar() -> None:
    """Real quirk found running a crypto backtest: some of CoinDCX's early history has a
    synthetic, flat, zero-volume filler bar timestamped ~1 second before the real one for
    that day. Un-deduped, this crashes prepare_market's reindex outright (pandas refuses
    duplicate axis labels) - and even where it didn't, would double-count the day."""
    import pandas as pd

    from tradedesk.backtest.runner import _dedupe_by_calendar_day

    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2019-01-06 05:30:00", tz=IST),
            pd.Timestamp("2019-01-07 05:29:59", tz=IST),  # phantom filler
            pd.Timestamp("2019-01-07 05:30:00", tz=IST),  # the real bar
            pd.Timestamp("2019-01-08 05:30:00", tz=IST),
        ],
        name="ts",
    )
    df = pd.DataFrame(
        {
            "open": [100.0, 105.0, 105.0, 103.0],
            "high": [102.0, 105.0, 107.0, 104.0],
            "low": [99.0, 105.0, 104.0, 101.0],
            "close": [101.0, 105.0, 103.0, 102.0],
            "volume": [5, 0, 3, 4],
        },
        index=idx,
    )
    out = _dedupe_by_calendar_day(df)
    assert len(out) == 3
    assert list(out.index.date) == [date(2019, 1, 6), date(2019, 1, 7), date(2019, 1, 8)]
    kept = out.loc[out.index.date == date(2019, 1, 7)]
    assert len(kept) == 1 and kept["volume"].iloc[0] == 3  # the real bar, not the phantom


def test_dedupe_by_calendar_day_is_a_noop_when_nothing_duplicated() -> None:

    from tradedesk.backtest.runner import _dedupe_by_calendar_day

    days = sessions(date(2026, 1, 1), 5)
    store = CandleStore()
    from tests.data.synth import daily
    from tradedesk.broker.indstocks.models import Instrument

    store.upsert_instruments(
        [Instrument(exch="NSE", segment="E", security_id="1", instrument_name="EQUITY",
                    trading_symbol="X", series="EQ")]
    )  # fmt: skip
    store.upsert_candles(daily("NSE_1", days))
    df = store.load("NSE_1", Interval.D1)
    out = _dedupe_by_calendar_day(df)
    assert len(out) == len(df) == 5
    store.close()


def test_prepare_market_survives_a_duplicate_day_reference_code() -> None:
    """End-to-end: a reference/benchmark code with the phantom-bar quirk must not crash
    prepare_market's closes_wide.reindex(calendar) call."""
    days = sessions(date(2024, 1, 1), 130)
    store = CandleStore()
    store.upsert_instruments([NIFTY])
    bench_candles = list(benchmark(REF, days))
    # inject a phantom filler bar 1 second before day 30's real bar, same shape as the
    # real CoinDCX quirk (flat OHLC at the previous close, zero volume)
    real = bench_candles[30]
    from datetime import timedelta as _td

    from tradedesk.broker.indstocks.models import Candle

    phantom = Candle(
        scrip_code=REF, interval=Interval.D1, ts=real.ts - _td(seconds=1),
        open=bench_candles[29].close, high=bench_candles[29].close,
        low=bench_candles[29].close, close=bench_candles[29].close, volume=0,
    )  # fmt: skip
    store.upsert_candles([*bench_candles, phantom])
    stocks = {"NSE_A": ("win", 1)}
    for code, (aftermath, seed) in stocks.items():
        store.upsert_candles(
            breakout_stock(code, days, breakout_index=100, aftermath=aftermath, seed=seed)
        )
    cfg = config(days[80], days[-1])
    md = prepare_market(store, list(stocks), REF, cfg)  # must not raise
    assert len(md.calendar) > 0
    store.close()
