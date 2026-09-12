"""CostModel: the market-agnostic seam over risk/costs.py (M13 §3, plan Phase 1).

Why a protocol at all: NSE's statutory cost shape (STT/stamp duty/GST/DP/SEBI, all rate-
and side-dependent) does not generalise to crypto (maker/taker fee + 1% TDS on the sell
leg, no equivalent of most of those lines). Rather than stretch `ChargeSchedule` to cover
both, every caller goes through this protocol instead of importing risk/costs.py's free
functions directly, so a second market can plug in a structurally different cost model
without any caller changing.

`EquityCostModel` is a THIN WRAPPER, not a reimplementation: every method calls straight
into the existing free function with the same arguments, so NSE costs are byte-identical
before and after this file exists. `tests/markets/test_costs.py` proves that with the same
property strategies as tests/unit/test_costs_properties.py, plus the 5 ledger-verified
golden notes routed through the wrapper.

`CryptoCostModel` (Phase 4) is the second model - CoinDCX's flat maker/taker fee + 1% TDS
(Section 194S) on the sell leg has no shape in common with NSE's statutory lines, so it
computes directly rather than delegating anywhere.

Neither model is wired into backtest/portfolio.py, cli.py's backtest/scan commands,
engine/filters.py or scan/evening_scan.py yet - those still call risk/costs.py's free
functions with a ChargeSchedule directly, unconditionally. That rewiring (plan §9) is
still open.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, runtime_checkable

from tradedesk.config.models import ChargeSchedule, CryptoChargeSchedule
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import LegCost, RoundTripCost
from tradedesk.risk.costs import leg_cost as _equity_leg_cost
from tradedesk.risk.costs import net_pnl as _equity_net_pnl
from tradedesk.risk.costs import net_r_multiple as _equity_net_r_multiple
from tradedesk.risk.costs import net_reward_risk as _equity_net_reward_risk
from tradedesk.risk.costs import round_trip_cost as _equity_round_trip_cost

PAISA = Decimal("0.01")


@runtime_checkable
class CostModel(Protocol):
    """Same five operations as risk/costs.py's free functions, minus the leading
    `schedule` argument - a CostModel is bound to one schedule for its lifetime.
    `dp_applies` is an equity-only concept (NSE's demat depository charge); a market with
    no such charge just ignores it. `slippage_pct` is exposed because BacktestConfig and
    scan_config need it and both schedules already carry it - reading `.schedule.slippage_pct`
    off a concrete instance works without this, but declaring it here makes it part of
    the contract instead of an implementation detail callers happen to rely on."""

    @property
    def slippage_pct(self) -> Decimal: ...

    def leg_cost(
        self, *, side: Side, trade_type: TradeType, qty: int, price: Decimal,
        dp_applies: bool = True,
    ) -> LegCost: ...  # fmt: skip

    def round_trip_cost(
        self, *, trade_type: TradeType, qty: int, entry_price: Decimal, exit_price: Decimal,
        dp_applies: bool = True,
    ) -> RoundTripCost: ...  # fmt: skip

    def net_pnl(
        self, *, trade_type: TradeType, qty: int, entry_price: Decimal, exit_price: Decimal,
        dp_applies: bool = True,
    ) -> Decimal: ...  # fmt: skip

    def net_r_multiple(
        self, *, trade_type: TradeType, qty: int, entry: Decimal, stop: Decimal, exit_price: Decimal
    ) -> Decimal: ...  # fmt: skip

    def net_reward_risk(
        self, *, trade_type: TradeType, qty: int, entry: Decimal, stop: Decimal, target: Decimal
    ) -> Decimal: ...  # fmt: skip


class EquityCostModel:
    """NSE cash-equity costs. Delegates every method to risk/costs.py unchanged - see the
    module docstring for why this must never duplicate that logic."""

    def __init__(self, schedule: ChargeSchedule) -> None:
        self.schedule = schedule

    @property
    def slippage_pct(self) -> Decimal:
        return self.schedule.slippage_pct

    def leg_cost(
        self, *, side: Side, trade_type: TradeType, qty: int, price: Decimal,
        dp_applies: bool = True,
    ) -> LegCost:  # fmt: skip
        return _equity_leg_cost(
            self.schedule, side=side, trade_type=trade_type, qty=qty, price=price,
            dp_applies=dp_applies,
        )  # fmt: skip

    def round_trip_cost(
        self, *, trade_type: TradeType, qty: int, entry_price: Decimal, exit_price: Decimal,
        dp_applies: bool = True,
    ) -> RoundTripCost:  # fmt: skip
        return _equity_round_trip_cost(
            self.schedule, trade_type=trade_type, qty=qty, entry_price=entry_price,
            exit_price=exit_price, dp_applies=dp_applies,
        )  # fmt: skip

    def net_pnl(
        self, *, trade_type: TradeType, qty: int, entry_price: Decimal, exit_price: Decimal,
        dp_applies: bool = True,
    ) -> Decimal:  # fmt: skip
        return _equity_net_pnl(
            self.schedule, trade_type=trade_type, qty=qty, entry_price=entry_price,
            exit_price=exit_price, dp_applies=dp_applies,
        )  # fmt: skip

    def net_r_multiple(
        self, *, trade_type: TradeType, qty: int, entry: Decimal, stop: Decimal, exit_price: Decimal
    ) -> Decimal:  # fmt: skip
        return _equity_net_r_multiple(
            self.schedule, trade_type=trade_type, qty=qty, entry=entry, stop=stop,
            exit_price=exit_price,
        )  # fmt: skip

    def net_reward_risk(
        self, *, trade_type: TradeType, qty: int, entry: Decimal, stop: Decimal, target: Decimal
    ) -> Decimal:  # fmt: skip
        return _equity_net_reward_risk(
            self.schedule, trade_type=trade_type, qty=qty, entry=entry, stop=stop, target=target,
        )  # fmt: skip


class CryptoCostModel:
    """CoinDCX + Indian crypto tax charges (M13 Phase 4). NOT a thin wrapper like
    EquityCostModel - the maths genuinely differ from NSE's, so this computes them
    directly. See config/markets/crypto.yaml and CryptoChargeSchedule's docstring for the
    verified numbers and their sources.

    Fields borrowed from LegCost (so this satisfies the same CostModel protocol as
    EquityCostModel with no new return type):
    - `brokerage` = the maker/taker trading fee (flat %, both sides - CoinDCX has no
      min/max caps the way NSE's brokerage does).
    - `stt` = 1% TDS (Section 194S), SELL leg only, on the full turnover regardless of
      profit or loss. Reusing the `stt` slot deliberately: both are a statutory line the
      exchange/broker must deduct on a sell, even though the government programmes
      behind them are unrelated.
    - `gst` = 18% GST on the trading fee only (not on TDS).
    - `exchange_txn`, `ipft`, `sebi_fee`, `stamp_duty`, `dp_charge` = 0 always (no
      crypto equivalent).

    `trade_type` and `dp_applies` are accepted for CostModel conformance and ignored:
    unlike NSE, TDS applies to every sell the same way regardless of hold duration, and
    there is no depository-charge concept to suppress.
    """

    def __init__(self, schedule: CryptoChargeSchedule) -> None:
        self.schedule = schedule

    @property
    def slippage_pct(self) -> Decimal:
        return self.schedule.slippage_pct

    def _round(self, value: Decimal) -> Decimal:
        if self.schedule.rounding == "paise":
            return value.quantize(PAISA, rounding=ROUND_HALF_UP)
        return value

    def leg_cost(
        self, *, side: Side, trade_type: TradeType, qty: int, price: Decimal,
        dp_applies: bool = True,
    ) -> LegCost:  # fmt: skip
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        turnover = Decimal(qty) * price
        fee = self._round(turnover * self.schedule.maker_taker_pct)
        gst = self._round(fee * self.schedule.gst_pct)
        tds = (
            self._round(turnover * self.schedule.tds_pct) if side is Side.SELL else Decimal("0.00")
        )
        zero = self._round(Decimal("0"))
        total = fee + gst + tds
        return LegCost(
            side=side, trade_type=trade_type, qty=qty, price=price, turnover=turnover,
            brokerage=fee, stt=tds, exchange_txn=zero, ipft=zero, sebi_fee=zero,
            stamp_duty=zero, gst=gst, dp_charge=zero, total=total,
        )  # fmt: skip

    def round_trip_cost(
        self, *, trade_type: TradeType, qty: int, entry_price: Decimal, exit_price: Decimal,
        dp_applies: bool = True,
    ) -> RoundTripCost:  # fmt: skip
        entry = self.leg_cost(side=Side.BUY, trade_type=trade_type, qty=qty, price=entry_price)
        exit_ = self.leg_cost(side=Side.SELL, trade_type=trade_type, qty=qty, price=exit_price)
        total = entry.total + exit_.total
        return RoundTripCost(
            entry=entry, exit=exit_, total=total, pct_of_entry_value=total / entry.turnover
        )

    def net_pnl(
        self, *, trade_type: TradeType, qty: int, entry_price: Decimal, exit_price: Decimal,
        dp_applies: bool = True,
    ) -> Decimal:  # fmt: skip
        rt = self.round_trip_cost(
            trade_type=trade_type, qty=qty, entry_price=entry_price, exit_price=exit_price
        )
        return (exit_price - entry_price) * qty - rt.total

    def net_r_multiple(
        self, *, trade_type: TradeType, qty: int, entry: Decimal, stop: Decimal, exit_price: Decimal
    ) -> Decimal:  # fmt: skip
        risk = _gross_risk(qty, entry, stop)
        pnl = self.net_pnl(trade_type=trade_type, qty=qty, entry_price=entry, exit_price=exit_price)
        return pnl / risk

    def net_reward_risk(
        self, *, trade_type: TradeType, qty: int, entry: Decimal, stop: Decimal, target: Decimal
    ) -> Decimal:  # fmt: skip
        _gross_risk(qty, entry, stop)
        if target <= entry:
            raise ValueError(f"target {target} must be above entry {entry} for a long")
        reward_net = self.net_pnl(
            trade_type=trade_type, qty=qty, entry_price=entry, exit_price=target
        )
        loss_net = -self.net_pnl(trade_type=trade_type, qty=qty, entry_price=entry, exit_price=stop)
        return reward_net / loss_net


def _gross_risk(qty: int, entry: Decimal, stop: Decimal) -> Decimal:
    risk = (entry - stop) * qty
    if risk <= 0:
        raise ValueError(f"stop {stop} must be below entry {entry} for a long")
    return risk
