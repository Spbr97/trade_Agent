"""Crypto intraday momentum/breakout research - a crypto-adapted sibling of
tradedesk_lab/intraday_research.py. No alerts, orders or promotion; same M14-M18 lab
discipline (never wired to production, never auto-promoted).

Built to answer a specific question, stated directly by the user: "ADA is up 4.91% in
13h, aim to predict these pathways" - can this project catch a sustained multi-hour
crypto move, not just evaluate a coin once a day off its daily candle?

Reuses intraday_research.py's tested primitives (`resolve_barriers`, `summarize`) and
mirrors its overall shape (signal generation through the production `scan_bar` causal
slicer via a temporary research registry, gap-aware fill simulation, a matched random-
timing null test, a full portfolio replay), with five adaptations the approved research
plan identified by reading both modules directly, not guessed:

1. No IST session-clock eligibility window (`_eligible_session`'s "09:45"-"14:45" filter)
   - a 24/7 market has no market hours to restrict to.
2. A max-hold-in-BARS convention (`MAX_HOLD_BARS`, H1 bars) instead of a same-session
   cap: a real move like the one that motivated this can and should be allowed to span a
   calendar-day boundary, which the NSE harness's `sessions_in`-bounded framing forbids.
3. `CryptoCostModel` instead of `EquityCostModel` (0.2% maker/taker fee, 1% Section 194S
   TDS on the sell leg, 18% GST on the fee - see markets/costs.py).
4. No sector mapping (`Portfolio.sector_of` defaults to an empty dict and simply never
   caps - no new code needed, confirmed by reading portfolio.py before writing this).
5. `Interval.H1` bars (CoinDCX has no M5; "up 4.91% in 13h" is an hourly-scale move, and
   13 H1 bars is roughly the whole thing - a scalp timeframe would not see it either).
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

import numpy as np
import pandas as pd

from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.broker.indstocks.models import Interval
from tradedesk.config import load_config
from tradedesk.config.models import RiskConfig
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.indicators import intraday_features
from tradedesk.engine.intraday_engine import IntradaySnapshot, scan_bar
from tradedesk.engine.intraday_signals import IntradayExitPlan, IntradaySignal
from tradedesk.markets.costs import CryptoCostModel
from tradedesk.markets.market import crypto_market
from tradedesk.models import TradeType, price_decimal
from tradedesk.risk.sizing import SizeInputs, position_size, quantize_qty
from tradedesk.setups.intraday import INTRADAY_REGISTRY, IntradaySetupContext
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.intraday_research import resolve_barriers, summarize

BAR = pd.Timedelta(hours=1)
TARGET_R = 2.0  # preregistered, same convention as the NSE harness - never searched
MAX_HOLD_BARS = 48  # ~48 real hours: room for a 13h+ move without an artificial same-day cap  # noqa: E501
WATCHLIST = [
    "CDX_BTCINR", "CDX_ETHINR", "CDX_SOLINR", "CDX_XRPINR", "CDX_DOGEINR",
    "CDX_ADAINR", "CDX_TRXINR", "CDX_XLMINR", "CDX_HBARINR", "CDX_BNBINR",
]  # fmt: skip


class CryptoSetupKind(StrEnum):
    BREAKOUT_RETEST = "crypto_breakout_retest"
    MOMENTUM_CONTINUATION = "crypto_momentum_continuation"


class CryptoResearchSignal(IntradaySignal):
    setup: CryptoSetupKind


def _geometry(
    kind: CryptoSetupKind,
    df: pd.DataFrame,
    ctx: IntradaySetupContext,
    stop: float,
    level: float,
    reasons: list[str],
) -> CryptoResearchSignal | None:
    last = df.iloc[-1]
    trigger, atr = float(last.high), float(last.atr14)
    risk = trigger - stop
    if risk <= 0 or risk > 3 * atr or atr <= 0 or not np.isfinite(risk + atr):
        return None
    at = pd.Timestamp(df.index[-1]) + BAR
    return CryptoResearchSignal(
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
        exit_plan=IntradayExitPlan(partial_fraction=1.0, max_hold_bars=MAX_HOLD_BARS),
        geometry={
            "reference_level": level,
            "planned_target_r": TARGET_R,
            "decision_at": at.isoformat(),
            "bar_open_at": str(df.index[-1]),
        },
        reasons=reasons,
    )


def _eligible_window(df: pd.DataFrame, lookback: int) -> pd.DataFrame | None:
    """A 24/7 market has no session clock; eligibility is "enough warmed-up bars", not
    an IST time-of-day window - the one NSE-specific eligibility gate this deliberately
    drops (see the module docstring, adaptation 1)."""
    if len(df) < lookback:
        return None
    window = df.iloc[-lookback:]
    row = window.iloc[-1]
    needed = (
        row.get("atr14"), row.get("vol_ratio20"), row.get("rsi14"), row.high, row.low, row.close,
    )  # fmt: skip
    if any(v is None or not np.isfinite(v) for v in needed):
        # Fails closed on real, non-hypothetical NaN warmup rows (the first ~14 bars of a
        # code's history, before rsi14/atr14 have enough data) rather than silently treating
        # a missing reading as "not exhausted"/"not extreme".
        return None
    if row.atr14 <= 0 or row.high <= row.low:
        return None
    return window


class BreakoutRetest:
    """Adapted from intraday_research.py's NSE version - conceptually the closest fit
    already to "catch a move early, near where it started breaking out": a level
    precedes a breakout, a later distinct bar retests and holds it. Scans a rolling
    lookback of the last 40 H1 bars instead of one calendar session."""

    kind = CryptoSetupKind.BREAKOUT_RETEST

    def arm(
        self, df: pd.DataFrame, ctx: IntradaySetupContext, params: dict[str, Any]
    ) -> CryptoResearchSignal | None:
        window = _eligible_window(df, lookback=40)
        if window is None:
            return None
        last = window.iloc[-1]
        for k in range(len(window) - 2, max(4, len(window) - 6) - 1, -1):
            prior = window.iloc[max(0, k - 12) : k]
            if len(prior) < 5:
                continue
            breakout = window.iloc[k]
            resistance = float(prior.high.max())
            tolerance = 0.15 * float(breakout.atr14)
            if not (
                breakout.close > resistance + tolerance
                and breakout.vol_ratio20 >= 1.2
                and breakout.close > breakout.open
                and breakout.atr14 > 0
            ):
                continue
            after = window.iloc[k + 1 :]
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
                window,
                ctx,
                stop,
                resistance,
                ["close broke established resistance", "a later bar retested and held resistance"],
            )
            if result is not None:
                return result.model_copy(
                    update={"geometry": {**result.geometry, "breakout_bar": str(window.index[k])}}
                )
        return None


class MomentumContinuation:
    """A genuinely different hypothesis from BreakoutRetest, not an adaptation of any
    existing NSE setup: N consecutive up-close bars confirmed by rising volume, entered
    on continuation past the most recent high - the literal "catch it while it's still
    moving" rule the ADA-13h question describes. Tested independently so this plan
    checks two hypotheses rather than only the one that sounds most obviously right."""

    kind = CryptoSetupKind.MOMENTUM_CONTINUATION
    n_up_bars = 3
    vol_ratio_min = 1.3
    max_extension_atr = 4.0  # reject if already this many ATRs above the window's own low

    def arm(
        self, df: pd.DataFrame, ctx: IntradaySetupContext, params: dict[str, Any]
    ) -> CryptoResearchSignal | None:
        window = _eligible_window(df, lookback=30)
        if window is None:
            return None
        recent = window.iloc[-self.n_up_bars :]
        if not (recent.close > recent.open).all():
            return None
        if not (recent.close.diff().dropna() > 0).all():
            return None
        last = recent.iloc[-1]
        if last.vol_ratio20 < self.vol_ratio_min:
            return None
        if last.rsi14 >= 80:  # rsi14 is already known finite - _eligible_window fails closed
            return None  # already exhausted, not "catching it early"
        window_low = float(window.iloc[-20:].low.min())
        if last.atr14 <= 0 or (last.close - window_low) / last.atr14 > self.max_extension_atr:
            return None
        stop = float(min(recent.low.min(), last.close - 1.5 * last.atr14))
        return _geometry(
            self.kind,
            window,
            ctx,
            stop,
            window_low,
            [
                f"{self.n_up_bars} consecutive up-closes on rising volume",
                "not yet overextended vs ATR",
            ],
        )


@contextmanager
def research_registry() -> Iterator[None]:
    """Scoped registration for a standalone process; restore even if evaluation fails."""
    additions = {
        CryptoSetupKind.BREAKOUT_RETEST: BreakoutRetest(),
        CryptoSetupKind.MOMENTUM_CONTINUATION: MomentumContinuation(),
    }
    if any(k in INTRADAY_REGISTRY for k in additions):
        raise RuntimeError("crypto research registry already active; use a separate process")
    INTRADAY_REGISTRY.update(additions)
    try:
        yield
    finally:
        for kind in additions:
            INTRADAY_REGISTRY.pop(kind, None)


@dataclass(frozen=True)
class ResolvedCandidate:
    signal: CryptoResearchSignal
    session: str  # calendar date (IST) of entry - portfolio bucketing key, not a hold cap
    decision_at: str
    entry_at: str
    exit_at: str
    entry_bar: int
    window_start: int
    window_end: int
    entry: float
    exit: float
    stop: float
    target: float
    gross_r: float
    target_hit: bool
    exit_reason: str


def fill_candidate(
    sig: CryptoResearchSignal,
    frame: pd.DataFrame,
    arm: int,
    slippage: float,
) -> tuple[ResolvedCandidate | None, str]:
    """A later CLOSED bar confirms continuation; enter the following bar's open, resolve
    up to `MAX_HOLD_BARS` later (or the end of history) - never bounded to a same-day
    session, since a real crypto move can span a calendar-day boundary (adaptation 2)."""
    last_idx = len(frame) - 1
    for k in range(arm + 1, min(last_idx, arm + sig.valid_bars) + 1):
        row = frame.iloc[k]
        if row.low <= sig.stop:
            return None, "invalidated_before_entry"
        if row.close < sig.trigger:
            continue
        j = k + 1
        if j > last_idx:
            return None, "no_next_bar"
        if frame.index[j] != frame.index[k] + BAR:
            return None, "missing_entry_bar"
        raw_entry = float(frame.iloc[j].open)
        entry = raw_entry * (1 + slippage)
        if entry <= sig.stop or sig.t1 <= entry:
            return None, "invalid_target_or_stop_at_fill"
        if raw_entry > sig.trigger + sig.chased_atr_mult * sig.atr:
            return None, "chased_at_fill"
        end = min(last_idx, j + MAX_HOLD_BARS)
        arrays = tuple(frame[c].to_numpy(float) for c in ("open", "high", "low", "close"))
        exit_bar, exit_price, reason = resolve_barriers(
            arrays, j, end, stop=sig.stop, target=sig.t1, slippage=slippage,
        )
        return ResolvedCandidate(
            signal=sig,
            session=str(pd.Timestamp(frame.index[j]).date()),
            decision_at=sig.armed_at.isoformat(),
            entry_at=frame.index[j].isoformat(),
            exit_at=(frame.index[exit_bar] + BAR).isoformat(),
            entry_bar=j,
            window_start=arm,
            window_end=end,
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
    costs: CryptoCostModel,
    qty_step: float,
    min_notional: float,
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
            qty_step=qty_step,
            min_notional=min_notional,
        )
    )
    qty = size.qty
    budget = equity * min(
        float(risk.max_risk_per_trade_pct),
        max(0.0, heat) if heat is not None else float(risk.max_risk_per_trade_pct),
    )
    # Fractional shrink (not a whole-unit decrement - NSE's `qty -= 1` would zero out a
    # sub-1-unit BTC/ETH quantity in a single step) until the cash-risk budget, including
    # estimated fees, is respected.
    while qty > 0 and _stop_loss(trade, qty, costs) > budget:
        qty = quantize_qty(qty * 0.9, qty_step)
    if not qty or qty * trade.entry < min_notional:
        return 0.0, 0.0, 0.0
    fees = float(
        costs.round_trip_cost(
            trade_type=TradeType.DELIVERY,
            qty=qty,
            entry_price=price_decimal(trade.entry),
            exit_price=price_decimal(trade.exit),
        ).total
    )
    pnl = (trade.exit - trade.entry) * qty - fees
    return qty, fees, pnl


def _stop_loss(trade: ResolvedCandidate, qty: float, costs: CryptoCostModel) -> float:
    stop_fill = trade.stop * (1 - float(costs.slippage_pct))
    fee = float(
        costs.round_trip_cost(
            trade_type=TradeType.DELIVERY,
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
    costs: CryptoCostModel,
    qty_step: float,
    min_notional: float,
) -> dict:
    qty, fees, pnl = _size_and_cost(trade, equity, risk, costs, qty_step, min_notional)
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


def portfolio_replay(
    trades: list[ResolvedCandidate],
    risk: RiskConfig,
    costs: CryptoCostModel,
    qty_step: float,
    min_notional: float,
    calendar: list[str],
) -> dict:
    """Chronological candidate replay through the existing Portfolio limits and
    settlement - no sector caps (empty `sector_of`, adaptation 4) and no no-entry-window
    check (24/7 market has no clock to restrict entries against)."""
    portfolio = Portfolio(risk, costs, float(risk.trading_capital), sector_of={})
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
                "session_close": FillReason.END,  # max-hold expiry, reusing resolve_barriers' label  # noqa: E501
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
            ok, reason = portfolio.can_enter(trade.signal, 1.0)
            if not ok:
                rejections[reason] += 1
                continue
            open_loss = sum(_stop_loss(t, p.qty_open, costs) for t, p in pending)
            available_heat = float(risk.max_portfolio_heat_pct) - open_loss / portfolio.equity
            qty, _, _ = _size_and_cost(
                trade, portfolio.equity, risk, costs, qty_step, min_notional, available_heat
            )
            committed = sum(p.entry_price * p.qty_open for p in portfolio.open.values())
            cash_left = max(0.0, portfolio.equity - committed)
            if trade.entry > 0 and qty * trade.entry > cash_left:
                qty = quantize_qty(cash_left / trade.entry, qty_step)
            if qty <= 0 or qty * trade.entry < min_notional:
                rejections["zero_size_or_cash"] += 1
                continue
            if trade.target * (1 - float(costs.slippage_pct)) <= trade.entry:
                rejections["target_below_entry_after_slippage"] += 1
                continue
            net_rr = costs.net_reward_risk(
                trade_type=TradeType.DELIVERY,
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
            row = _trade_row(trade, portfolio.equity, risk, costs, qty_step, min_notional)
            actual_costs = float(
                costs.round_trip_cost(
                    trade_type=TradeType.DELIVERY,
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
            "configured minimum net reward/risk",
            "CoinDCX min notional and qty step",
        ],
        "not_evaluated": [
            "sector caps (no crypto sector mapping exists)",
            "no-entry-window (24/7 market has no clock to restrict against)",
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
    seed: int = 20260919,
) -> dict:
    """Same symbol, same risk distance and realised target multiple, a random H1 bar
    instead of the real entry - the same discipline `intraday_research.py::matched_random`
    uses, minus its IST clock-window filter (adaptation 1) and bounded by
    `MAX_HOLD_BARS` from the random entry rather than a session end (adaptation 2)."""
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
        last_idx = len(frame) - 1
        positions = np.arange(30, max(31, last_idx - 1))
        if not len(positions):
            continue
        distance = trade.entry - trade.stop
        reward = trade.target - trade.entry
        for cohort, j in enumerate(rng.choice(positions, size=n_cohorts)):
            entry = float(arrays[0][j]) * (1 + slippage)
            end = min(last_idx, int(j) + MAX_HOLD_BARS)
            _, exit_price, _ = resolve_barriers(
                arrays,
                int(j),
                end,
                stop=entry - distance,
                target=entry + reward,
                slippage=slippage,
            )
            means[cohort] += (exit_price - entry) / distance
        used += 1
        real_r.append(trade.gross_r)
    if not used:
        return {"n": 0, "status": "no matching windows"}
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


def _read_frames(root: Path, bars: int | None) -> tuple[dict, dict]:
    frames, symbols = {}, {}
    with CandleStore(root / "data/crypto.duckdb") as store:
        for code in WATCHLIST:
            raw = store.load(code, Interval.H1)
            if raw.empty:
                continue
            if bars is not None and len(raw) > bars + 40:
                raw = raw.iloc[-(bars + 40) :]  # indicator warmup, not evaluated
            features = intraday_features(raw)
            if bars is not None and len(features) > bars:
                features = features.iloc[-bars:]
            frames[code] = features
            symbols[code] = code.removeprefix("CDX_")
    return frames, symbols


def run_research(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    bars: int | None = 4000,
    n_cohorts: int = 200,
) -> dict:
    """Evaluate the two crypto candidates against existing H1 history; persist only
    research outputs (own `data/m14_m18/crypto_intraday_research/` namespace)."""
    if bars is not None and bars < 1:
        raise ValueError("bars must be positive or None")
    if n_cohorts < 20:
        raise ValueError("n_cohorts must be at least 20")
    frames, symbols = _read_frames(root, bars)
    if not frames:
        raise RuntimeError(
            "no crypto H1 history found; run "
            "`tradedesk data load --market crypto --interval 60minute <watchlist>` first"
        )
    settings = load_config(root)
    market = crypto_market(settings)
    costs = market.costs
    assert isinstance(costs, CryptoCostModel)
    slip = float(costs.slippage_pct)
    calendar = sorted({str(d) for f in frames.values() for d in f.index.date})
    signals: Counter = Counter()
    rejects = {kind.value: Counter() for kind in CryptoSetupKind}
    candidates: dict[str, list[ResolvedCandidate]] = {k.value: [] for k in CryptoSetupKind}
    quality: Counter = Counter()
    with research_registry():
        for code, frame in frames.items():
            n = len(frame)
            if n < 45:
                quality["skipped_too_short"] += 1
                continue
            ohlc = frame[["open", "high", "low", "close"]]
            if (
                not np.isfinite(ohlc.to_numpy()).all()
                or (ohlc <= 0).any().any()
                or (frame.high < ohlc[["open", "close", "low"]].max(axis=1)).any()
                or (frame.low > ohlc[["open", "close", "high"]].min(axis=1)).any()
                or (frame.volume < 0).any()
            ):
                quality["skipped_invalid_ohlcv"] += 1
                continue
            quality["evaluated_codes"] += 1
            for i in range(40, n - 2):
                snapshot = IntradaySnapshot(
                    at=frame.index[i] + BAR,
                    arming_interval=Interval.H1,
                    features={code: frame.iloc[: i + 1]},
                    symbols=symbols,
                )
                for sig in scan_bar(
                    snapshot, list(CryptoSetupKind), {}, max_hold_bars=MAX_HOLD_BARS
                ):
                    kind = sig.setup.value
                    signals[kind] += 1
                    candidate, reason = fill_candidate(sig, frame, i, slip)
                    if candidate is not None:
                        candidates[kind].append(candidate)
                    else:
                        rejects[kind][reason] += 1
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    strategies = {}
    for kind, trades in candidates.items():
        rows = [
            _trade_row(
                t,
                float(settings.risk.trading_capital),
                settings.risk,
                costs,
                market.qty_step,
                market.min_notional_inr,
            )
            for t in trades
        ]
        strategies[kind] = {
            "armed_signals": signals[kind],
            "executable_candidates": len(trades),
            "entry_rejections": dict(rejects[kind]),
            "opportunity_diagnostic": summarize(rows, calendar),
            "portfolio": portfolio_replay(
                trades, settings.risk, costs, market.qty_step, market.min_notional_inr, calendar
            ),
            "matched_random": matched_random(trades, frames, n_cohorts=n_cohorts, slippage=slip),
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
        "market": "crypto",
        "interval": Interval.H1.value,
        "target_r": TARGET_R,
        "max_hold_bars": MAX_HOLD_BARS,
        "bars_requested": bars,
        "session_count": len(calendar),
        "from": calendar[0] if calendar else None,
        "through": calendar[-1] if calendar else None,
        "symbols": symbols,
        "bars": sum(len(f) for f in frames.values()),
        "data_quality": dict(quality),
        "strategies": strategies,
        "limitations": [
            "Existing stored crypto H1 watchlist only (10 pairs), not the full universe.",
            "Historical diagnostic, no untouched test period or prospective evidence.",
            "Opportunity rows may overlap and are not an investable portfolio return.",
            "Portfolio applies documented caps but has no sector mapping or 24/7 regime gate.",
            "OHLC cannot establish intrabar ordering: stop wins target ties.",
            "CryptoCostModel (0.2% fee + 1% TDS on the sell leg + 18% GST on the fee); "
            "slippage is estimated.",
            "Only completed bar confirmation, then next open; no same-bar retroactive fills.",
            "A profitable max-hold-expiry exit is not a target-hit success.",
            "Wilson bounds (via summarize/opportunity_diagnostic) assume independent calls; "
            "correlated rows weaken that evidence.",
            "No multi-timeframe alignment computed - only H1 has been backfilled for crypto "
            "so far, so setups cannot reject on a higher-timeframe conflict yet.",
            f"Max hold is {MAX_HOLD_BARS} H1 bars (~{MAX_HOLD_BARS}h); a move needing longer "
            "would be cut off unresolved at that point.",
        ],
    }
    destination = output / "crypto_intraday_research" / run_id / "report.json"
    write_json(destination, report)
    write_json(output / "crypto_intraday_research/latest.json", report)
    return report
