"""Phase 3's "separate, simpler validation loop" (the design the user chose over
provisionally registering an unapproved candidate into production's `SetupKind`/
`REGISTRY`): a single-position-per-stock daily-bar simulator for `LabSetup` candidates,
built from the exact same tested fill/exit primitives the real backtester uses
(`backtest/fills.py::evaluate_entry`/`evaluate_exit`, `risk/sizing.py::position_size`),
without the full multi-setup portfolio engine (no position/sector/heat caps - a disclosed
simplification appropriate for validating one candidate in isolation, not for a
live-ready portfolio simulation).

Produces real `backtest.portfolio.ClosedTrade` records - the exact same type
`run_self_review_candidate.py::_to_harness_trade` already knows how to adapt into
`HarnessTrade`s for the harness gauntlet, reused unmodified here too.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradedesk.backtest.fills import Bar, EntryOutcome, Position, evaluate_entry, evaluate_exit
from tradedesk.backtest.portfolio import ClosedTrade
from tradedesk.risk.sizing import SizeInputs, position_size
from tradedesk.setups.base import SetupContext
from tradedesk_lab.candidates.base import LabSetup, LabSignal
from tradedesk_lab.harness.trade import HarnessTrade

SLIPPAGE_PCT = 0.0005
ARM_WARMUP_BARS = 60


def _to_closed_trade(setup_name: str, code: str, position: Position) -> ClosedTrade | None:
    exit_date = position.exit_date
    if exit_date is None:
        return None
    gross = position.gross_pnl()
    costs = 0.0  # real round-trip costs are applied by the caller before this trade is used
    net_pnl = gross - costs
    r_multiple = net_pnl / position.initial_risk if position.initial_risk > 0 else 0.0
    exit_reason = position.fills[-1].reason.value if position.fills else "end"
    gap_fills = [f for f in position.fills if f.reason.value == "gap_stop"]
    gap_damage = 0.0
    if gap_fills:
        planned_loss = position.initial_risk
        actual_loss = (position.entry_price - gap_fills[0].price) * gap_fills[0].qty
        gap_damage = max(0.0, actual_loss - planned_loss)
    return ClosedTrade(
        position=position,
        costs=costs,
        net_pnl=net_pnl,
        r_multiple=r_multiple,
        setup=setup_name,
        scrip_code=code,
        entry_date=position.entry_date,
        exit_date=exit_date,
        sessions_held=position.sessions_held,
        exit_reason=exit_reason,
        gap_damage=gap_damage,
    )


def _apply_real_costs(trade: ClosedTrade, costs_model: Any) -> ClosedTrade:
    """Recomputes net_pnl/r_multiple with the real round-trip cost model, matching how
    every other real result in this project accounts for costs - the zero-cost value
    `_to_closed_trade` fills in is only a placeholder until this runs."""
    from tradedesk.models import TradeType, price_decimal

    position = trade.position
    qty = position.qty_initial
    entry_price = position.entry_price
    exit_price = position.fills[-1].price if position.fills else entry_price
    round_trip = costs_model.round_trip_cost(
        trade_type=TradeType.DELIVERY,
        qty=qty,
        entry_price=price_decimal(entry_price),
        exit_price=price_decimal(exit_price),
    )
    real_costs = float(round_trip.total)
    net_pnl = trade.position.gross_pnl() - real_costs
    r_multiple = net_pnl / position.initial_risk if position.initial_risk > 0 else 0.0
    trade.costs = real_costs
    trade.net_pnl = net_pnl
    trade.r_multiple = r_multiple
    return trade


def simulate_single_setup(
    candidate: LabSetup,
    frames: dict[str, pd.DataFrame],
    symbols: dict[str, str],
    *,
    costs_model: Any,
    equity: float,
    risk_pct: float = 0.005,
    qty_step: float = 1.0,
    params: dict[str, Any] | None = None,
) -> list[ClosedTrade]:
    """Walks each stock's own daily feature frame independently (no cross-stock portfolio
    limits - see module docstring), arming/filling/exiting one position at a time per
    stock via the real, tested `evaluate_entry`/`evaluate_exit`/`position_size`."""

    params = params or {}
    trades: list[ClosedTrade] = []
    # Candidates built from causal rolling/shift expressions (tradedesk_lab/candidates/
    # rules.py) precompute their signal column once per frame instead of re-evaluating it
    # on every causal slice - same values, verified by tests_lab/test_rule_candidates.py.
    prepare = getattr(candidate, "prepare", None)
    gate_column = getattr(candidate, "gate_column", None)

    for code, frame in frames.items():
        if prepare is not None:
            frame = prepare(frame)
        ctx = SetupContext(scrip_code=code, symbol=symbols.get(code, code))
        n = len(frame)
        position: Position | None = None
        pending: tuple[LabSignal, int] | None = None
        i = ARM_WARMUP_BARS
        # Plain arrays instead of a pandas row lookup per bar (the dominant cost), and a
        # precomputed gate a candidate can't arm without - bars where it is False are
        # skipped while flat, since arm() would return None there anyway.
        cols = {
            name: frame[name].to_numpy(float)
            for name in ("open", "high", "low", "close", "ema10", "atr14")
        }
        volume = frame["volume"].fillna(0).to_numpy() if "volume" in frame else None
        dates = [pd.Timestamp(ts).date() for ts in frame.index]
        gate = frame[gate_column].to_numpy(bool) if gate_column in frame.columns else None

        while i < n:
            if position is None and pending is None and gate is not None and not gate[i]:
                i += 1
                continue
            bar = Bar(
                on=dates[i],
                open=cols["open"][i],
                high=cols["high"][i],
                low=cols["low"][i],
                close=cols["close"][i],
                volume=int(volume[i] or 0) if volume is not None else 0,
                ema10=cols["ema10"][i],
                atr=cols["atr14"][i],
            )

            if position is not None:
                entry_day = position.entry_date == bar.on
                evaluate_exit(position, bar, SLIPPAGE_PCT, entry_day=entry_day)
                if position.closed:
                    closed = _to_closed_trade(candidate.name, code, position)
                    if closed is not None:
                        trades.append(_apply_real_costs(closed, costs_model))
                    position = None
                i += 1
                continue

            if pending is not None:
                sig, waited = pending
                outcome, price = evaluate_entry(sig, bar, SLIPPAGE_PCT)
                if outcome == EntryOutcome.FILLED and price is not None:
                    sizing = position_size(
                        SizeInputs(
                            equity=equity,
                            entry=price,
                            stop=sig.stop,
                            max_risk_pct=risk_pct,
                            max_position_value_pct=1.0,
                            qty_step=qty_step,
                        )
                    )
                    if sizing.viable:
                        position = Position(
                            signal=sig,
                            entry_date=bar.on,
                            entry_price=price,
                            qty_initial=sizing.qty,
                            qty_open=sizing.qty,
                            stop=sig.stop,
                            qty_step=qty_step,
                        )
                    pending = None
                elif outcome in (EntryOutcome.CHASED, EntryOutcome.INVALIDATED):
                    pending = None
                else:
                    waited += 1
                    pending = (sig, waited) if waited < sig.valid_sessions else None
                i += 1
                continue

            causal = frame.iloc[: i + 1]
            sig = candidate.arm(causal, ctx, params)
            if sig is not None:
                pending = (sig, 0)
            i += 1

    return trades


def to_harness_trades(
    trades: list[ClosedTrade], frames: dict[str, pd.DataFrame]
) -> tuple[list[HarnessTrade], dict[str, tuple[Any, Any, Any, Any]]]:
    """Adapts `simulate_single_setup`'s real `ClosedTrade`s into `HarnessTrade`s for
    `run_gauntlet`, plus the `bars_by_code` OHLC arrays the random-entry benchmark needs -
    both derived from the same `frames` already used for simulation, so the positional
    indices line up exactly with the bars the random-entry benchmark will redraw against.
    """

    date_to_position: dict[str, dict[Any, int]] = {
        code: {pd.Timestamp(ts).date(): pos for pos, ts in enumerate(frame.index)}
        for code, frame in frames.items()
    }
    harness_trades: list[HarnessTrade] = []
    for trade in trades:
        positions = date_to_position.get(trade.scrip_code)
        if positions is None:
            continue
        entry_bar = positions.get(trade.entry_date)
        exit_bar = positions.get(trade.exit_date)
        if entry_bar is None or exit_bar is None:
            continue
        risk = trade.position.entry_price - trade.position.signal.stop
        if risk <= 0:
            continue
        gross_r = trade.r_multiple + trade.costs / trade.position.initial_risk
        harness_trades.append(
            HarnessTrade(
                scrip_code=trade.scrip_code,
                entry_at=trade.entry_date.isoformat(),
                exit_at=trade.exit_date.isoformat(),
                entry_bar=entry_bar,
                window_start=entry_bar,
                window_end=exit_bar,
                entry=trade.position.entry_price,
                stop=trade.position.signal.stop,
                target=trade.position.signal.t2,
                gross_r=gross_r,
                net_r=trade.r_multiple,
            )
        )
    bars_by_code = {
        code: (
            frame["open"].to_numpy(float),
            frame["high"].to_numpy(float),
            frame["low"].to_numpy(float),
            frame["close"].to_numpy(float),
        )
        for code, frame in frames.items()
    }
    return harness_trades, bars_by_code
