"""Offline, same-session research candidates; no alerts, orders or promotion.

Signal generation goes through the production ``scan_bar`` causal slicer using a
temporary research registry. Run this module in a separate research process, never
inside the live scanner. All persisted results have their own research namespace.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd
import yaml

from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.backtest.null_baseline import sessions_in
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.broker.indstocks.models import Interval
from tradedesk.config import load_config
from tradedesk.config.models import RiskConfig
from tradedesk.engine.indicators import intraday_features
from tradedesk.engine.intraday_engine import IntradaySnapshot, scan_bar
from tradedesk.engine.intraday_signals import IntradayExitPlan, IntradaySignal
from tradedesk.markets.costs import EquityCostModel
from tradedesk.models import TradeType, price_decimal
from tradedesk.reliability import wilson_lower_bound
from tradedesk.risk.sizing import SizeInputs, position_size
from tradedesk.setups.intraday import INTRADAY_REGISTRY, IntradaySetupContext
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

BAR = pd.Timedelta(minutes=5)
TARGET_R = 2.0  # preregistered, never searched to inflate hit rate


class ResearchSetupKind(StrEnum):
    SUPPORT_REVERSAL = "support_resistance_reversal"
    BREAKOUT_RETEST = "breakout_retest"


class ResearchSignal(IntradaySignal):
    setup: ResearchSetupKind


def _geometry(
    kind: ResearchSetupKind,
    df: pd.DataFrame,
    ctx: IntradaySetupContext,
    stop: float,
    level: float,
    reasons: list[str],
) -> ResearchSignal | None:
    last = df.iloc[-1]
    trigger, atr = float(last.high), float(last.atr14)
    risk = trigger - stop
    if risk <= 0 or risk > 3 * atr or atr <= 0 or not np.isfinite(risk + atr):
        return None
    at = pd.Timestamp(df.index[-1]) + BAR
    return ResearchSignal(
        id=f"{kind.value}:{ctx.scrip_code}:{at.isoformat()}",
        scrip_code=ctx.scrip_code,
        symbol=ctx.symbol,
        setup=kind,
        interval=ctx.interval,
        armed_at=at,
        trigger=trigger,
        stop=stop,
        t1=trigger + TARGET_R * risk,
        t2=trigger + TARGET_R * risk,
        atr=atr,
        valid_bars=3,
        chased_atr_mult=0.5,
        exit_plan=IntradayExitPlan(partial_fraction=1.0, max_hold_bars=75),
        geometry={
            "reference_level": level,
            "planned_target_r": TARGET_R,
            "decision_at": at.isoformat(),
            "bar_open_at": str(df.index[-1]),
        },
        reasons=reasons,
    )


def _eligible_session(df: pd.DataFrame) -> pd.DataFrame | None:
    if df.empty:
        return None
    stamp = pd.Timestamp(df.index[-1])
    clock = (stamp + BAR).strftime("%H:%M")
    if clock < "09:45" or clock > "14:45":
        return None
    session_bar = int(df.iloc[-1].get("session_bar", len(df) - 1))
    session = df.iloc[-session_bar - 1 :]
    if len(session) < 7:
        return None
    row = session.iloc[-1]
    needed = (row.get("atr14"), row.get("vol_ratio20"), row.high, row.low, row.close)
    if any(v is None or not np.isfinite(v) for v in needed):
        return None
    if row.atr14 <= 0 or row.high <= row.low or row.vol_ratio20 < 1.0:
        return None
    if row.close <= row.open or (row.close - row.low) / (row.high - row.low) < 0.65:
        return None
    return session


class SupportResistanceReversal:
    """Prior support is defined before the rejection bar, then reclaimed on volume."""

    kind = ResearchSetupKind.SUPPORT_REVERSAL

    def arm(
        self,
        df: pd.DataFrame,
        ctx: IntradaySetupContext,
        params: dict[str, Any],
    ) -> ResearchSignal | None:
        session = _eligible_session(df)
        if session is None or (ctx.alignment is not None and ctx.alignment.conflict):
            return None
        prior, last = session.iloc[-21:-1], session.iloc[-1]
        support = float(prior.low.min())
        tolerance = 0.15 * float(last.atr14)
        # At least two distinct prior bars established the level; the rejection is later.
        if int((prior.low <= support + tolerance).sum()) < 2:
            return None
        if not (support - tolerance <= last.low <= support + tolerance < last.close):
            return None
        if last.close <= session.iloc[-2].close:
            return None
        stop = float(min(last.low, support) - 0.1 * last.atr14)
        return _geometry(
            self.kind,
            session,
            ctx,
            stop,
            support,
            ["prior support tested at least twice", "bullish rejection closes above support"],
        )


class BreakoutRetest:
    """Resistance precedes breakout; a distinct later bar retests and closes above it."""

    kind = ResearchSetupKind.BREAKOUT_RETEST

    def arm(
        self,
        df: pd.DataFrame,
        ctx: IntradaySetupContext,
        params: dict[str, Any],
    ) -> ResearchSignal | None:
        session = _eligible_session(df)
        if session is None or (ctx.alignment is not None and ctx.alignment.conflict):
            return None
        last = session.iloc[-1]
        # Never call the breakout's own low a retest: require a distinct later bar.
        for k in range(len(session) - 2, max(4, len(session) - 5) - 1, -1):
            prior = session.iloc[max(0, k - 12) : k]
            if len(prior) < 5:
                continue
            breakout = session.iloc[k]
            resistance = float(prior.high.max())
            tolerance = 0.15 * float(breakout.atr14)
            if not (
                breakout.close > resistance + tolerance
                and breakout.vol_ratio20 >= 1.2
                and breakout.close > breakout.open
                and breakout.atr14 > 0
            ):
                continue
            after = session.iloc[k + 1 :]
            if (after.close < resistance - tolerance).any():
                continue
            if not (
                resistance - tolerance <= last.low <= resistance + tolerance
                and last.close > resistance + tolerance
            ):
                continue
            stop = float(min(last.low, resistance) - 0.1 * last.atr14)
            result = _geometry(
                self.kind,
                session,
                ctx,
                stop,
                resistance,
                ["close broke established resistance", "a later bar retested and held resistance"],
            )
            if result is not None:
                return result.model_copy(
                    update={
                        "geometry": {
                            **result.geometry,
                            "breakout_bar": str(session.index[k]),
                        }
                    }
                )
        return None


@contextmanager
def research_registry() -> Iterator[None]:
    """Scoped registration for a standalone process; restore even if evaluation fails."""
    additions = {
        ResearchSetupKind.SUPPORT_REVERSAL: SupportResistanceReversal(),
        ResearchSetupKind.BREAKOUT_RETEST: BreakoutRetest(),
    }
    if any(k in INTRADAY_REGISTRY for k in additions):
        raise RuntimeError("research registry already active; use a separate process")
    INTRADAY_REGISTRY.update(additions)
    try:
        yield
    finally:
        for kind in additions:
            INTRADAY_REGISTRY.pop(kind, None)


@dataclass(frozen=True)
class ResolvedCandidate:
    signal: ResearchSignal
    session: str
    decision_at: str
    entry_at: str
    exit_at: str
    entry_bar: int
    session_start: int
    session_end: int
    entry: float
    exit: float
    stop: float
    target: float
    gross_r: float
    target_hit: bool
    exit_reason: str


def resolve_barriers(
    arrays: tuple[np.ndarray, ...],
    start: int,
    end: int,
    *,
    stop: float,
    target: float,
    slippage: float,
) -> tuple[int, float, str]:
    """Entry occurs at ``start`` OPEN; that bar's entire range is therefore subsequent."""
    opens, highs, lows, closes = arrays
    for k in range(start, end + 1):
        if opens[k] <= stop:
            return k, float(opens[k]) * (1 - slippage), "gap_stop"
        if lows[k] <= stop:
            return k, stop * (1 - slippage), "stop"
        if highs[k] >= target:
            # Target is a limit: ignore favourable gaps and debit estimated slippage.
            return k, target * (1 - slippage), "target"
    return end, float(closes[end]) * (1 - slippage), "session_close"


def fill_candidate(
    sig: ResearchSignal,
    frame: pd.DataFrame,
    arm: int,
    start: int,
    end: int,
    slippage: float,
) -> tuple[ResolvedCandidate | None, str]:
    """A later CLOSED bar confirms continuation; enter the following bar's open.

    The arming and confirmation bars cannot hit the trade's stop or target. Their
    ranges occurred before entry. Invalid/chased/missing fills never become successes.
    """
    for k in range(arm + 1, min(end, arm + sig.valid_bars) + 1):
        row = frame.iloc[k]
        if row.low <= sig.stop:
            return None, "invalidated_before_entry"
        if row.close < sig.trigger:
            continue
        j = k + 1
        if j > end:
            return None, "no_next_session_bar"
        if frame.index[j] != frame.index[k] + BAR:
            return None, "missing_entry_bar"
        raw_entry = float(frame.iloc[j].open)
        entry = raw_entry * (1 + slippage)
        if entry <= sig.stop or sig.t1 <= entry:
            return None, "invalid_target_or_stop_at_fill"
        if raw_entry > sig.trigger + sig.chased_atr_mult * sig.atr:
            return None, "chased_at_fill"
        arrays = tuple(frame[c].to_numpy(float) for c in ("open", "high", "low", "close"))
        exit_bar, exit_price, reason = resolve_barriers(
            arrays,
            j,
            end,
            stop=sig.stop,
            target=sig.t1,
            slippage=slippage,
        )
        return ResolvedCandidate(
            signal=sig,
            session=str(pd.Timestamp(frame.index[j]).date()),
            decision_at=sig.armed_at.isoformat(),
            entry_at=frame.index[j].isoformat(),
            exit_at=(frame.index[exit_bar] + BAR).isoformat(),
            entry_bar=j,
            session_start=start,
            session_end=end,
            entry=entry,
            exit=exit_price,
            stop=sig.stop,
            target=sig.t1,
            gross_r=(exit_price - entry) / (entry - sig.stop),
            target_hit=reason == "target",
            exit_reason=reason,
        ), "filled"
    return None, "expired_unconfirmed"


def _size_and_cost(
    trade: ResolvedCandidate,
    equity: float,
    risk: RiskConfig,
    costs: EquityCostModel,
    heat: float | None = None,
) -> tuple[float, float, float]:
    size = position_size(
        SizeInputs(
            equity=equity,
            entry=trade.entry,
            stop=trade.stop,
            max_risk_pct=float(risk.max_risk_per_trade_pct),
            max_position_value_pct=float(risk.max_position_value_pct),
            available_heat_pct=heat,
        )
    )
    qty = size.qty
    # Include estimated stop-exit slippage and both fees in the maximum cash loss.
    budget = equity * min(
        float(risk.max_risk_per_trade_pct),
        max(0.0, heat) if heat is not None else float(risk.max_risk_per_trade_pct),
    )
    while qty and _stop_loss(trade, qty, costs) > budget:
        qty -= 1
    if not qty:
        return 0.0, 0.0, 0.0
    fees = float(
        costs.round_trip_cost(
            trade_type=TradeType.INTRADAY,
            qty=qty,
            entry_price=price_decimal(trade.entry),
            exit_price=price_decimal(trade.exit),
        ).total
    )
    pnl = (trade.exit - trade.entry) * qty - fees
    return qty, fees, pnl


def _stop_loss(trade: ResolvedCandidate, qty: float, costs: EquityCostModel) -> float:
    stop_fill = trade.stop * (1 - float(costs.slippage_pct))
    fee = float(
        costs.round_trip_cost(
            trade_type=TradeType.INTRADAY,
            qty=qty,
            entry_price=price_decimal(trade.entry),
            exit_price=price_decimal(stop_fill),
        ).total
    )
    return (trade.entry - stop_fill) * qty + fee


def _trade_row(
    trade: ResolvedCandidate,
    equity: float,
    risk: RiskConfig,
    costs: EquityCostModel,
) -> dict:
    qty, fees, pnl = _size_and_cost(trade, equity, risk, costs)
    data = asdict(trade)
    data["signal"] = trade.signal.model_dump(mode="json")
    data.update(
        qty=qty,
        costs=fees,
        net_pnl=pnl,
        net_r=pnl / (qty * (trade.entry - trade.stop)) if qty else None,
        success=bool(qty and trade.target_hit and pnl > 0),
    )
    return data


def summarize(rows: list[dict], calendar: list[str]) -> dict:
    filled = [r for r in rows if r["qty"] > 0]
    n = len(filled)
    wins = sum(r["success"] for r in filled)
    by_date: dict[str, list[dict]] = {d: [] for d in calendar}
    for row in filled:
        by_date.setdefault(row["session"], []).append(row)
    daily = [
        {
            "session": d,
            "calls": len(trades),
            "successes": sum(t["success"] for t in trades),
            "success_rate": sum(t["success"] for t in trades) / len(trades) if trades else None,
            "net_pnl": sum(t["net_pnl"] for t in trades),
        }
        for d, trades in sorted(by_date.items())
    ]
    active = [d for d in daily if d["calls"]]
    return {
        "trades": n,
        "successes": wins,
        "success_rate": wins / n if n else None,
        "wilson_lower_95": wilson_lower_bound(wins, n),
        "target_hit_rate": sum(r["target_hit"] for r in filled) / n if n else None,
        "net_win_rate": sum(r["net_pnl"] > 0 for r in filled) / n if n else None,
        "mean_net_r": float(np.mean([r["net_r"] for r in filled])) if n else None,
        "net_pnl": sum(r["net_pnl"] for r in filled),
        "costs": sum(r["costs"] for r in filled),
        "sessions": len(daily),
        "active_sessions": len(active),
        "no_call_sessions": len(daily) - len(active),
        "active_sessions_at_70": sum(d["success_rate"] >= 0.7 for d in active),
        "active_sessions_at_80": sum(d["success_rate"] >= 0.8 for d in active),
        "daily": daily,
    }


def portfolio_replay(
    trades: list[ResolvedCandidate],
    risk: RiskConfig,
    costs: EquityCostModel,
    sectors: dict[str, str],
    calendar: list[str],
) -> dict:
    """Chronological candidate replay through existing Portfolio limits and settlement.

    Research target is fixed at 2R before fills. Current configured net-RR entry gate
    still applies here, so candidates with insufficient net reward are rejected.
    """
    portfolio = Portfolio(risk, costs, float(risk.trading_capital), sector_of=sectors)
    pending: list[tuple[ResolvedCandidate, Position]] = []
    selected: list[dict] = []
    rejections: Counter = Counter()
    groups: dict[str, list[ResolvedCandidate]] = {}
    for trade in trades:
        groups.setdefault(trade.session, []).append(trade)

    def settle_before(at: str) -> None:
        for trade, pos in sorted(pending.copy(), key=lambda x: x[0].exit_at):
            if trade.exit_at > at:
                continue
            reason = {
                "stop": FillReason.STOP,
                "gap_stop": FillReason.GAP_STOP,
                "target": FillReason.PARTIAL,
                "session_close": FillReason.END,
            }[trade.exit_reason]
            fill = Fill(pos.entry_date, trade.exit, pos.qty_open, reason)
            pos.fills.append(fill)
            pos.qty_open = 0
            portfolio.record_fills(pos, [fill])
            pending.remove((trade, pos))

    for si, day in enumerate(calendar):
        portfolio.start_session(pd.Timestamp(day).date(), si)
        for trade in sorted(groups.get(day, []), key=lambda t: (t.entry_at, t.signal.id)):
            settle_before(trade.entry_at)
            clock = pd.Timestamp(trade.entry_at).strftime("%H:%M")
            if risk.no_entry_window.start <= clock < risk.no_entry_window.end:
                rejections["no_entry_window"] += 1
                continue
            ok, reason = portfolio.can_enter(trade.signal, 1.0)
            if not ok:
                rejections[reason] += 1
                continue
            open_loss = sum(_stop_loss(t, p.qty_open, costs) for t, p in pending)
            available_heat = float(risk.max_portfolio_heat_pct) - open_loss / portfolio.equity
            qty, _, _ = _size_and_cost(
                trade,
                portfolio.equity,
                risk,
                costs,
                available_heat,
            )
            committed = sum(p.entry_price * p.qty_open for p in portfolio.open.values())
            qty = min(qty, float(max(0, int((portfolio.equity - committed) / trade.entry))))
            if qty <= 0:
                rejections["zero_size_or_cash"] += 1
                continue
            if trade.target * (1 - float(costs.slippage_pct)) <= trade.entry:
                rejections["target_below_entry_after_slippage"] += 1
                continue
            net_rr = costs.net_reward_risk(
                trade_type=TradeType.INTRADAY,
                qty=qty,
                entry=price_decimal(trade.entry),
                stop=price_decimal(trade.stop * (1 - float(costs.slippage_pct))),
                target=price_decimal(trade.target * (1 - float(costs.slippage_pct))),
            )
            if net_rr < risk.min_net_rr:
                rejections["configured_min_net_rr"] += 1
                continue
            pos = Position(
                trade.signal,
                pd.Timestamp(day).date(),
                trade.entry,
                qty,
                qty,
                trade.stop,
                fills=[Fill(pd.Timestamp(day).date(), trade.entry, qty, FillReason.ENTRY)],
            )
            portfolio.open_position(pos)
            pending.append((trade, pos))
            row = _trade_row(trade, portfolio.equity, risk, costs)
            # Cash availability can lower the initial per-opportunity quantity.
            actual_costs = float(
                costs.round_trip_cost(
                    trade_type=TradeType.INTRADAY,
                    qty=qty,
                    entry_price=price_decimal(trade.entry),
                    exit_price=price_decimal(trade.exit),
                ).total
            )
            net = (trade.exit - trade.entry) * qty - actual_costs
            row.update(
                qty=qty,
                costs=actual_costs,
                net_pnl=net,
                net_r=net / (qty * (trade.entry - trade.stop)),
                success=bool(trade.target_hit and net > 0),
            )
            selected.append(row)
        settle_before(f"{day}T23:59:59+05:30")
        portfolio.mark_to_market({})
    initial = float(risk.trading_capital)
    equity = np.array([initial, *[e for _, e in portfolio.equity_curve]])
    summary = summarize(selected, calendar)
    return {
        **summary,
        "initial_capital": initial,
        "ending_capital": portfolio.equity,
        "max_drawdown": float(np.max(1 - equity / np.maximum.accumulate(equity))),
        "rejections": dict(rejections),
        "rows": selected,
        "enforced": [
            "risk sizing including estimated stop slippage and fees",
            "position value",
            "cash",
            "portfolio heat including estimated fees",
            "max positions",
            "daily entries",
            "weekly realised loss limit",
            "consecutive loss pause",
            "stop reentry cooldown",
            "sector caps",
            "no entry window",
            "configured minimum net reward/risk",
        ],
        "not_evaluated": [
            "daily market regime/VIX gate",
            "results-event blackout",
            "live spread/depth/latency",
            "mark-to-market intrabar loss limit",
        ],
        "scope": "Diagnostic capped replay; not a production-eligibility decision.",
    }


def matched_random(
    trades: list[ResolvedCandidate],
    frames: dict[str, pd.DataFrame],
    *,
    n_cohorts: int,
    slippage: float,
    seed: int = 20260916,
) -> dict:
    """Same symbol/session, same risk distance and realised target multiple.

    Identical next-open/barrier/slippage rules for real and random trades. This is
    a gross-R timing diagnostic; portfolio selection and fee rounding are separate.
    """
    if not trades:
        return {"n": 0, "status": "no executable candidates"}
    rng = np.random.default_rng(seed)
    means = np.zeros(n_cohorts)
    cached = {
        code: tuple(f[c].to_numpy(float) for c in ("open", "high", "low", "close"))
        for code, f in frames.items()
    }
    used = 0
    real_r = []
    for trade in trades:
        arrays = cached[trade.signal.scrip_code]
        frame = frames[trade.signal.scrip_code]
        positions = np.arange(trade.session_start + 1, trade.session_end)
        positions = positions[
            (frame.index[positions].strftime("%H:%M") >= "09:50")
            & (frame.index[positions].strftime("%H:%M") <= "15:05")
        ]
        if not len(positions):
            continue
        distance = trade.entry - trade.stop
        reward = trade.target - trade.entry
        for cohort, j in enumerate(rng.choice(positions, size=n_cohorts)):
            entry = float(arrays[0][j]) * (1 + slippage)
            _, exit_price, _ = resolve_barriers(
                arrays,
                int(j),
                trade.session_end,
                stop=entry - distance,
                target=entry + reward,
                slippage=slippage,
            )
            means[cohort] += (exit_price - entry) / distance
        used += 1
        real_r.append(trade.gross_r)
    if not used:
        return {"n": 0, "status": "no matching sessions"}
    means /= used
    actual = float(np.mean(real_r))
    p = float((1 + np.sum(means >= actual)) / (n_cohorts + 1))
    return {
        "n": used,
        "cohorts": n_cohorts,
        "seed": seed,
        "setup_mean_gross_r": actual,
        "null_mean_gross_r": float(means.mean()),
        "gross_r_lift": actual - float(means.mean()),
        "p_value": p,
        "beats_random_at_05": p < 0.05,
        "scope": "Gross R timing diagnostic; costs/portfolio reported separately.",
    }


def _read_frames(root: Path, sessions: int | None) -> tuple[dict, dict]:
    frames, symbols = {}, {}
    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        codes = con.execute(
            "SELECT DISTINCT scrip_code FROM candles WHERE interval=? "
            "AND scrip_code LIKE 'NSE_%' ORDER BY 1",
            [Interval.M5.value],
        ).fetchall()
        for (code,) in codes:
            raw = con.execute(
                "SELECT ts, open, high, low, close, volume FROM candles "
                "WHERE scrip_code=? AND interval=? ORDER BY ts",
                [code, Interval.M5.value],
            ).df()
            idx = pd.to_datetime(raw.pop("ts"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
            raw.index = pd.DatetimeIndex(idx)
            spans = sessions_in(raw.index)
            if sessions is not None and len(spans) > sessions + 5:
                raw = raw.iloc[spans[-sessions - 5][0] :]  # indicator warmup, not evaluated
            features = intraday_features(raw)
            if sessions is not None:
                spans = sessions_in(features.index)
                if len(spans) > sessions:
                    features = features.iloc[spans[-sessions][0] :]
            frames[code] = features
            row = con.execute(
                "SELECT trading_symbol FROM instruments WHERE scrip_code=?",
                [code],
            ).fetchone()
            symbols[code] = row[0] if row else code
    return frames, symbols


def run_research(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    sessions: int | None = 120,
    n_cohorts: int = 200,
) -> dict:
    """Evaluate fixed candidates against existing M5 history; persist only research outputs."""
    if sessions is not None and sessions < 1:
        raise ValueError("sessions must be positive or None")
    if n_cohorts < 20:
        raise ValueError("n_cohorts must be at least 20")
    frames, symbols = _read_frames(root, sessions)
    settings = load_config(root)
    costs = EquityCostModel(settings.risk.costs)
    slip = float(costs.slippage_pct)
    calendar = sorted({str(d) for f in frames.values() for d in f.index.date})
    signals: Counter = Counter()
    rejects = {kind.value: Counter() for kind in ResearchSetupKind}
    candidates: dict[str, list[ResolvedCandidate]] = {k.value: [] for k in ResearchSetupKind}
    quality: Counter = Counter()
    with research_registry():
        for code, frame in frames.items():
            for start, end in sessions_in(frame.index):
                # At most 75 bars supplied to scan_bar; no quadratic full-history slicing.
                session_frame = frame.iloc[start : end + 1]
                if (
                    len(session_frame) != 75
                    or session_frame.index[0].strftime("%H:%M") != "09:15"
                    or session_frame.index[-1].strftime("%H:%M") != "15:25"
                    or not (session_frame.index.to_series().diff().iloc[1:] == BAR).all()
                ):
                    quality["skipped_incomplete_or_nonstandard_symbol_sessions"] += 1
                    continue
                ohlc = session_frame[["open", "high", "low", "close"]]
                if (
                    not np.isfinite(ohlc.to_numpy()).all()
                    or (ohlc <= 0).any().any()
                    or (session_frame.high < ohlc[["open", "close", "low"]].max(axis=1)).any()
                    or (session_frame.low > ohlc[["open", "close", "high"]].min(axis=1)).any()
                    or (session_frame.volume < 0).any()
                ):
                    quality["skipped_invalid_ohlcv_symbol_sessions"] += 1
                    continue
                quality["complete_symbol_sessions"] += 1
                for i in range(start + 6, end - 1):
                    snapshot = IntradaySnapshot(
                        at=frame.index[i] + BAR,
                        arming_interval=Interval.M5,
                        features={code: session_frame},
                        symbols=symbols,
                    )
                    for sig in scan_bar(snapshot, list(ResearchSetupKind), {}):
                        kind = sig.setup.value
                        signals[kind] += 1
                        candidate, reason = fill_candidate(sig, frame, i, start, end, slip)
                        if candidate is not None:
                            candidates[kind].append(candidate)
                        else:
                            rejects[kind][reason] += 1
    mapping = yaml.safe_load((root / "config/sector_membership.yaml").read_text("utf-8"))
    sector_by_symbol = {s: sector for sector, members in mapping.items() for s in members}
    # Unknown members share a conservative bucket rather than bypassing sector limits.
    sectors = {code: sector_by_symbol.get(symbol, "UNKNOWN") for code, symbol in symbols.items()}
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    strategies = {}
    for kind, trades in candidates.items():
        rows = [
            _trade_row(t, float(settings.risk.trading_capital), settings.risk, costs)
            for t in trades
        ]
        strategies[kind] = {
            "armed_signals": signals[kind],
            "executable_candidates": len(trades),
            "entry_rejections": dict(rejects[kind]),
            "opportunity_diagnostic": summarize(rows, calendar),
            "portfolio": portfolio_replay(trades, settings.risk, costs, sectors, calendar),
            "matched_random": matched_random(
                trades,
                frames,
                n_cohorts=n_cohorts,
                slippage=slip,
            ),
            "rows": rows,
        }
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "strategy_source_sha256": digest(Path(__file__)),
        "risk_configuration": settings.risk.model_dump(mode="json"),
        "created_at": datetime.now(UTC).isoformat(),
        "status": "historical_diagnostic_only",
        "eligible_for_live": False,
        "target_r": TARGET_R,
        "sessions_requested": sessions,
        "session_count": len(calendar),
        "from": calendar[0] if calendar else None,
        "through": calendar[-1] if calendar else None,
        "symbols": symbols,
        "bars": sum(len(f) for f in frames.values()),
        "data_quality": dict(quality),
        "strategies": strategies,
        "limitations": [
            "Existing stored NSE M5 symbols only; selected survivorship-biased universe.",
            "Historical diagnostic, no untouched test period or prospective evidence.",
            "Opportunity rows may overlap and are not an investable portfolio return.",
            "Portfolio applies documented caps but lacks daily VIX/regime and results gates.",
            "OHLC cannot establish intrabar ordering: stop wins target ties.",
            "Intraday charges are the existing provisional cost schedule; slippage is estimated.",
            "Only completed bar confirmation, then next open; no same-bar retroactive fills.",
            "A profitable session-close exit is not a target-hit success.",
            "Wilson bounds assume independent calls; correlated rows weaken that evidence.",
            "Unknown sectors share one conservative capped bucket.",
            "Only complete regular 09:15-15:30 sessions evaluated; gaps/short sessions excluded.",
            "Gap-through-stop losses can exceed the sized stop-loss cash budget.",
        ],
    }
    destination = output / "intraday_research" / run_id / "report.json"
    write_json(destination, report)
    write_json(output / "intraday_research/latest.json", report)
    return report
