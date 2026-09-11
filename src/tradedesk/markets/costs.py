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

Nothing in the codebase is rewired to use this yet (backtest/portfolio.py, cli.py,
engine/filters.py and scan/evening_scan.py still call risk/costs.py directly) - that
happens when config/markets/*.yaml and a --market flag exist to select between models
(plan §9), not before there is a second model to select.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradedesk.config.models import ChargeSchedule
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import LegCost, RoundTripCost
from tradedesk.risk.costs import leg_cost as _equity_leg_cost
from tradedesk.risk.costs import net_pnl as _equity_net_pnl
from tradedesk.risk.costs import net_r_multiple as _equity_net_r_multiple
from tradedesk.risk.costs import net_reward_risk as _equity_net_reward_risk
from tradedesk.risk.costs import round_trip_cost as _equity_round_trip_cost


@runtime_checkable
class CostModel(Protocol):
    """Same five operations as risk/costs.py's free functions, minus the leading
    `schedule` argument - a CostModel is bound to one schedule for its lifetime.
    `dp_applies` is an equity-only concept (NSE's demat depository charge); a market with
    no such charge just ignores it."""

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
