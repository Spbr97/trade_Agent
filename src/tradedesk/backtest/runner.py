"""Event-driven backtester (PLAN.md 11): drives the live engine code with a historical
clock. One session at a time: exits for held positions, then entries for armed signals
(portfolio limits applied in time order), then mark-to-market, then the evening scan
that arms tomorrow's signals.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from tradedesk.backtest.fills import (
    Bar,
    EntryOutcome,
    Fill,
    FillReason,
    Position,
    evaluate_entry,
    evaluate_exit,
)
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config.models import EngineConfig, RiskConfig
from tradedesk.data.candle_store import CandleStore, ist_dates
from tradedesk.data.universe import UniverseRules
from tradedesk.engine.engine import MarketSnapshot, scan_day
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.lifecycle import SignalState, TrackedSignal
from tradedesk.engine.regime import RegimeSnapshot, breadth_above_ema, classify_regime
from tradedesk.engine.relative_strength import rs_rank
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.live.confirmation import Decision, confirm_trigger, is_chased
from tradedesk.live.models import IntradayBar, SessionRules
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size


@dataclass
class BacktestConfig:
    setups: Sequence[SetupKind]
    start: date
    end: date
    capital: float
    risk: RiskConfig
    engine: EngineConfig
    setup_params: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    universe_rules: UniverseRules = UniverseRules()
    sector_of: Mapping[str, str] = field(default_factory=dict)
    slippage_pct: float = 0.0005
    warmup_sessions: int = 260
    vix_code: str | None = None
    session_rules: SessionRules = SessionRules()
    use_intraday: bool = True  # confirm on 15-minute bars when the store has them


@dataclass
class MarketData:
    """Everything the loop needs, precomputed once. All series are causal, so slicing a
    frame at a date is equivalent to having computed it on that date."""

    calendar: list[date]
    features: dict[str, pd.DataFrame]
    symbols: dict[str, str]
    pos_by_date: dict[str, dict[date, int]]
    benchmark: pd.DataFrame
    rs_rank: pd.DataFrame  # index date, columns codes
    breadth: pd.Series  # index date
    vix: pd.Series | None  # index date
    universe_by_month: dict[tuple[int, int], list[str]]
    results_dates: dict[str, list[date]]
    intraday: dict[str, dict[date, list[IntradayBar]]] = field(default_factory=dict)


@dataclass
class BacktestResult:
    config: BacktestConfig
    portfolio: Portfolio
    signals: list[TrackedSignal]
    calendar: list[date]

    @property
    def equity(self) -> pd.Series:
        idx, vals = (
            zip(*self.portfolio.equity_curve, strict=True)
            if self.portfolio.equity_curve
            else ((), ())
        )
        return pd.Series(list(vals), index=pd.Index(list(idx), name="date"), dtype=float)


# ------------------------------------------------------------------ preparation


def _date_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.Index(ist_dates(df), name="date")
    return out


def prepare_market(
    store: CandleStore,
    codes: Sequence[str],
    benchmark_code: str,
    cfg: BacktestConfig,
) -> MarketData:
    from tradedesk.data.universe import month_starts, trading_days

    load_from = datetime.combine(cfg.start, time.min, tzinfo=IST) - timedelta(
        days=int(cfg.warmup_sessions * 1.6) + 30
    )
    load_to = datetime.combine(cfg.end + timedelta(days=1), time.min, tzinfo=IST)
    calendar = trading_days(store, benchmark_code, load_from.date(), cfg.end)
    bench = store.load(benchmark_code, Interval.D1, load_from, load_to, adjusted=False)

    features: dict[str, pd.DataFrame] = {}
    symbols: dict[str, str] = {}
    pos_by_date: dict[str, dict[date, int]] = {}
    closes: dict[str, pd.Series] = {}
    turnover: dict[str, pd.Series] = {}
    for code in codes:
        df = store.load(code, Interval.D1, load_from, load_to, adjusted=True)
        if len(df) < 30:
            continue
        feats = daily_features(df)
        features[code] = feats
        symbols[code] = store.symbol_for(code) or code
        dates = ist_dates(df)
        pos_by_date[code] = {d: i for i, d in enumerate(dates)}
        closes[code] = pd.Series(df["close"].to_numpy(), index=pd.Index(dates))
        turnover[code] = pd.Series((df["close"] * df["volume"]).to_numpy(), index=pd.Index(dates))

    closes_wide = pd.DataFrame(closes).reindex(calendar)
    bench_dates = pd.Series(bench["close"].to_numpy(), index=pd.Index(ist_dates(bench)))
    rs = rs_rank(closes_wide, bench_dates.reindex(calendar), cfg.engine.relative_strength)
    breadth = breadth_above_ema(closes_wide, cfg.engine.regime.breadth_ema)

    vix: pd.Series | None = None
    if cfg.vix_code:
        v = store.load(cfg.vix_code, Interval.D1, load_from, load_to, adjusted=False)
        if not v.empty:
            vix = pd.Series(v["close"].to_numpy(), index=pd.Index(ist_dates(v)))

    turn_wide = pd.DataFrame(turnover).reindex(calendar)
    rules = cfg.universe_rules
    avg_turn = turn_wide.rolling(
        rules.lookback_sessions, min_periods=rules.min_sessions_present
    ).mean()
    universe_by_month: dict[tuple[int, int], list[str]] = {}
    for d in month_starts(calendar):
        if d not in avg_turn.index:
            continue
        liquid = avg_turn.loc[d].to_numpy() >= rules.min_avg_turnover_inr
        priced = closes_wide.loc[d].to_numpy() >= rules.min_price
        cols = list(closes_wide.columns)
        universe_by_month[(d.year, d.month)] = sorted(
            c for c, a, b in zip(cols, liquid, priced, strict=True) if bool(a) and bool(b)
        )

    intraday: dict[str, dict[date, list[IntradayBar]]] = {}
    if cfg.use_intraday:
        for code in features:
            m15 = store.load(code, Interval.M15, load_from, load_to, adjusted=True)
            if m15.empty:
                continue
            by_day: dict[date, list[IntradayBar]] = {}
            starts = [t.to_pydatetime() for t in pd.DatetimeIndex(m15.index)]
            ohlc = {k: m15[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}
            vols = m15["volume"].to_numpy(dtype="int64")
            for j, start in enumerate(starts):
                bar = IntradayBar(
                    scrip_code=code,
                    start=start,
                    end=start + timedelta(minutes=15),
                    open=float(ohlc["open"][j]),
                    high=float(ohlc["high"][j]),
                    low=float(ohlc["low"][j]),
                    close=float(ohlc["close"][j]),
                    volume=int(vols[j]),
                )
                by_day.setdefault(start.date(), []).append(bar)
            intraday[code] = by_day

    results_dates: dict[str, list[date]] = {}
    for code, symbol in symbols.items():
        rows = store.con.execute(
            "SELECT event_date FROM results_events WHERE symbol = ? ORDER BY event_date", [symbol]
        ).fetchall()
        if rows:
            results_dates[code] = [r[0] for r in rows]

    return MarketData(
        calendar=calendar,
        features=features,
        symbols=symbols,
        pos_by_date=pos_by_date,
        benchmark=_date_index(bench),
        rs_rank=rs,
        breadth=breadth,
        vix=vix,
        universe_by_month=universe_by_month,
        results_dates=results_dates,
        intraday=intraday,
    )


# ------------------------------------------------------------------------ loop


def _bar(feats: pd.DataFrame, i: int, on: date) -> Bar:
    row = feats.iloc[i]
    return Bar(
        on=on,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]),
        ema10=float(row["ema10"]),
        atr=float(row["atr14"]) if pd.notna(row["atr14"]) else 0.0,
    )


def _sessions_until_results(
    dates: list[date] | None, on: date, calendar: list[date], cal_pos: int
) -> int | None:
    if not dates:
        return None
    k = bisect.bisect_right(dates, on)
    if k >= len(dates):
        return None
    nxt = dates[k]
    j = bisect.bisect_left(calendar, nxt)
    return j - cal_pos


def regime_on(md: MarketData, on: date, cfg: BacktestConfig) -> RegimeSnapshot | None:
    bench = md.benchmark.loc[:on]
    if len(bench) < cfg.engine.regime.nifty_ema + 5:
        return None
    breadth = md.breadth.get(on)
    vix = md.vix.loc[:on] if md.vix is not None else None
    return classify_regime(
        bench.set_index(pd.DatetimeIndex([pd.Timestamp(d, tz=IST) for d in bench.index])),
        breadth_pct=float(breadth) if breadth is not None and pd.notna(breadth) else None,
        vix=vix,
        cfg=cfg.engine.regime,
        on=on,
    )


def evaluate_entry_intraday(
    sig: Signal, bars: Sequence[IntradayBar], rules: SessionRules, slippage_pct: float
) -> tuple[EntryOutcome, float | None, int]:
    """Same decision as the live monitor: chased on the day's open, else the first 15-minute
    bar that closes above the trigger. Returns (outcome, fill price, index of the fill bar)."""
    if not bars:
        return EntryOutcome.NONE, None, -1
    if is_chased(sig, bars[0].open):
        return EntryOutcome.CHASED, None, -1
    for i, bar in enumerate(bars):
        c = confirm_trigger(sig, bar, rules)
        if c.decision is Decision.TRIGGERED and c.fill_price is not None:
            return EntryOutcome.FILLED, c.fill_price * (1 + slippage_pct), i
        if c.decision is Decision.INVALIDATED:
            return EntryOutcome.INVALIDATED, None, i
    return EntryOutcome.NONE, None, -1


def _post_fill_bar(bars: Sequence[IntradayBar], fill_index: int, daily: Bar) -> Bar | None:
    """The rest of the entry day after the fill bar, as one Bar for the exit rules."""
    rest = list(bars[fill_index + 1 :])
    if not rest:
        return None
    return Bar(
        on=daily.on,
        open=rest[0].open,
        high=max(b.high for b in rest),
        low=min(b.low for b in rest),
        close=rest[-1].close,
        volume=sum(b.volume for b in rest),
        ema10=daily.ema10,
        atr=daily.atr,
    )


def universe_for(md: MarketData, on: date) -> list[str] | None:
    """Monthly universe in force on `on` (the most recent rebuild at or before it)."""
    key = (on.year, on.month)
    if key in md.universe_by_month:
        return md.universe_by_month[key]
    earlier = [k for k in md.universe_by_month if k <= key]
    return md.universe_by_month[max(earlier)] if earlier else None


def build_snapshot(
    md: MarketData,
    on: date,
    cfg: BacktestConfig,
    *,
    regime: RegimeSnapshot | None,
    exclude: set[str] | None = None,
) -> MarketSnapshot:
    """Everything known at the close of `on`, built the same way for the backtester and
    the live evening scan (that shared construction is what the parity test checks)."""
    exclude = exclude or set()
    cal_pos = bisect.bisect_left(md.calendar, on)
    universe = universe_for(md, on)
    candidates = [
        c for c in (universe if universe is not None else md.features) if c not in exclude
    ]
    sliced = {
        c: md.features[c].iloc[: md.pos_by_date[c][on] + 1]
        for c in candidates
        if on in md.pos_by_date.get(c, {})
    }
    rs_row = md.rs_rank.loc[on] if on in md.rs_rank.index else None
    return MarketSnapshot(
        on=on,
        features=sliced,
        symbols=md.symbols,
        rs_percentile={
            c: float(rs_row[c]) for c in sliced if rs_row is not None and pd.notna(rs_row.get(c))
        },
        regime=regime,
        results_in_sessions={
            c: _sessions_until_results(md.results_dates.get(c), on, md.calendar, cal_pos)
            for c in sliced
        },
        universe=list(sliced),
    )


def run_backtest(md: MarketData, cfg: BacktestConfig) -> BacktestResult:
    portfolio = Portfolio(
        risk=cfg.risk, costs=cfg.risk.costs, equity=cfg.capital, sector_of=cfg.sector_of
    )
    tracked: list[TrackedSignal] = []
    live_by_code: dict[str, TrackedSignal] = {}  # one live (armed/open) signal per code
    sessions = [d for d in md.calendar if cfg.start <= d <= cfg.end]
    prev_regime: RegimeSnapshot | None = None
    prev_regime_mult = 1.0

    for si, on in enumerate(sessions):
        portfolio.start_session(on, si)
        # 1. exits for held positions
        for code, pos in list(portfolio.open.items()):
            i = md.pos_by_date[code].get(on)
            if i is None:
                continue
            fills = evaluate_exit(pos, _bar(md.features[code], i, on), cfg.slippage_pct)
            if pos.closed:
                portfolio.record_fills(pos, fills)
                ts = live_by_code.pop(code, None)
                if ts is not None:
                    ts.move(SignalState.CLOSED, on, fills[-1].reason.value)

        # 2. entries for armed signals (in deterministic order)
        armed = sorted(
            (t for t in tracked if t.state is SignalState.ARMED and t.signal.armed_on < on),
            key=lambda t: (t.signal.setup.value, t.signal.scrip_code),
        )
        for ts in armed:
            sig = ts.signal
            ts.tick_session()
            code = sig.scrip_code
            i = md.pos_by_date[code].get(on)
            if i is None:
                if ts.expired_by_time:
                    ts.move(SignalState.EXPIRED, on)
                    live_by_code.pop(code, None)
                continue
            bar = _bar(md.features[code], i, on)
            day_bars = md.intraday.get(code, {}).get(on) if md.intraday else None
            fill_index = -1
            if day_bars:
                outcome, price, fill_index = evaluate_entry_intraday(
                    sig, day_bars, cfg.session_rules, cfg.slippage_pct
                )
                if outcome is EntryOutcome.NONE and bar.close < sig.stop:
                    outcome = EntryOutcome.INVALIDATED
            else:
                outcome, price = evaluate_entry(sig, bar, cfg.slippage_pct)
            if outcome is EntryOutcome.CHASED:
                ts.move(SignalState.CHASED, on, f"open {bar.open:.2f} > trigger + ATR")
                live_by_code.pop(code, None)
                continue
            if outcome is EntryOutcome.INVALIDATED:
                ts.move(SignalState.INVALIDATED, on, f"close {bar.close:.2f} < stop")
                live_by_code.pop(code, None)
                continue
            if outcome is EntryOutcome.NONE:
                if ts.expired_by_time:
                    ts.move(SignalState.EXPIRED, on)
                    live_by_code.pop(code, None)
                continue
            assert price is not None
            ts.move(SignalState.TRIGGERED, on, f"filled {price:.2f}")
            ok, why = portfolio.can_enter(sig, prev_regime_mult)
            if not ok:
                ts.move(SignalState.SKIPPED, on, why)
                portfolio.rejections.append((on, sig.id, why))
                live_by_code.pop(code, None)
                continue
            size = position_size(
                SizeInputs(
                    equity=portfolio.equity,
                    entry=price,
                    stop=sig.stop,
                    max_risk_pct=float(cfg.risk.max_risk_per_trade_pct),
                    max_position_value_pct=float(cfg.risk.max_position_value_pct),
                    size_multiplier=prev_regime_mult,
                    gap_risk_cap_pct=float(cfg.risk.gap_risk_cap_pct),
                    gap95_pct=gap95_pct(md.features[code].iloc[: i + 1]),
                    available_heat_pct=portfolio.available_heat_pct(),
                )
            )
            if not size.viable:
                ts.move(SignalState.SKIPPED, on, "size 0: " + ", ".join(size.caps))
                portfolio.rejections.append((on, sig.id, "size 0"))
                live_by_code.pop(code, None)
                continue
            pos = Position(
                signal=sig,
                entry_date=on,
                entry_price=price,
                qty_initial=size.qty,
                qty_open=size.qty,
                stop=sig.stop,
                highest_close=price,
                fills=[Fill(on=on, price=price, qty=size.qty, reason=FillReason.ENTRY)],
            )
            ts.move(SignalState.TAKEN, on, f"qty {size.qty} " + ", ".join(size.caps))
            ts.move(SignalState.OPEN, on)
            portfolio.open_position(pos)
            entry_bar = _post_fill_bar(day_bars, fill_index, bar) if day_bars else bar
            fills = (
                evaluate_exit(pos, entry_bar, cfg.slippage_pct, entry_day=True)
                if entry_bar is not None
                else []
            )
            if pos.closed:
                portfolio.record_fills(pos, fills)
                ts.move(SignalState.CLOSED, on, fills[-1].reason.value)
                live_by_code.pop(code, None)

        # 3. mark to market at the close
        closes = {
            code: float(md.features[code].iloc[i]["close"])
            for code in portfolio.open
            if (i := md.pos_by_date[code].get(on)) is not None
        }
        portfolio.mark_to_market(closes)

        # 4. evening scan -> tomorrow's armed signals
        regime = regime_on(md, on, cfg)
        snapshot = build_snapshot(md, on, cfg, regime=regime, exclude=set(live_by_code))
        for sig in scan_day(
            snapshot, cfg.setups, cfg.setup_params, max_hold_sessions=cfg.risk.max_hold_sessions
        ):
            if sig.scrip_code in live_by_code:
                continue
            ts = TrackedSignal(signal=sig)
            tracked.append(ts)
            live_by_code[sig.scrip_code] = ts
        prev_regime = regime
        prev_regime_mult = regime.size_multiplier if regime else 1.0

    # 5. close whatever is still open at the last session's close
    if sessions:
        last = sessions[-1]
        for code, pos in list(portfolio.open.items()):
            i = md.pos_by_date[code].get(last)
            if i is None:
                i = max(j for d, j in md.pos_by_date[code].items() if d <= last)
            bar = _bar(md.features[code], i, last)
            fill = Fill(
                on=last,
                price=bar.close * (1 - cfg.slippage_pct),
                qty=pos.qty_open,
                reason=FillReason.END,
            )
            pos.fills.append(fill)
            pos.qty_open = 0
            portfolio.record_fills(pos, [fill])
            ts = live_by_code.pop(code, None)
            if ts is not None:
                ts.move(SignalState.CLOSED, last, "end")
    _ = prev_regime
    return BacktestResult(config=cfg, portfolio=portfolio, signals=tracked, calendar=sessions)
