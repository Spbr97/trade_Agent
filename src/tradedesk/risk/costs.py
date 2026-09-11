"""Cost calculator for NSE cash-equity trades (PLAN.md 1.6).

Pure and deterministic: `Decimal` in, `Decimal` out, no I/O. Everything downstream
(sizing, net R:R, the paper book, the backtester) gets its charges from here.

Charge rules encoded:
- Brokerage: % of order value clamped to [min, max] per executed order (IND: 0.1%, Rs 2-5).
- STT: delivery on both legs; intraday on the sell leg only.
- Exchange transaction charge and SEBI fee: both legs, on turnover.
- Stamp duty: buy leg only; rate depends on trade type.
- GST: on brokerage + exchange transaction charge + SEBI fee.
- DP charge: delivery sell legs only, once per scrip per day (`dp_applies` lets the
  caller suppress it for a second partial exit on the same day); carries its own GST.

The calculator never decides whether a trade is intraday or delivery - the caller passes
`TradeType`. A position bought and sold on the same day is INTRADAY.

Net reward:risk convention (matches the trade card in PLAN.md 7):
    net R:R = (gross reward - costs to target) / (gross risk + costs to stop)
Costs make a stop-out lose more than 1R and a target gain less than its gross R.
"""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict

from tradedesk.config.models import ChargeSchedule
from tradedesk.models import Side, TradeType

__all__ = [
    "LegCost",
    "RoundTripCost",
    "leg_cost",
    "net_pnl",
    "net_r_multiple",
    "net_reward_risk",
    "round_trip_cost",
]

PAISA = Decimal("0.01")
ZERO = Decimal("0")


class LegCost(BaseModel):
    """Charges on a single executed order."""

    model_config = ConfigDict(frozen=True)

    side: Side
    trade_type: TradeType
    qty: int
    price: Decimal
    turnover: Decimal
    brokerage: Decimal
    stt: Decimal
    exchange_txn: Decimal
    sebi_fee: Decimal
    stamp_duty: Decimal
    gst: Decimal
    dp_charge: Decimal
    total: Decimal


class RoundTripCost(BaseModel):
    """Charges on an entry order plus its exit order."""

    model_config = ConfigDict(frozen=True)

    entry: LegCost
    exit: LegCost
    total: Decimal
    pct_of_entry_value: Decimal


def _round(value: Decimal, schedule: ChargeSchedule) -> Decimal:
    if schedule.rounding == "paise":
        return value.quantize(PAISA, rounding=ROUND_HALF_UP)
    return value


def _check_order(qty: int, price: Decimal) -> None:
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")


def leg_cost(
    schedule: ChargeSchedule,
    *,
    side: Side,
    trade_type: TradeType,
    qty: int,
    price: Decimal,
    dp_applies: bool = True,
) -> LegCost:
    """Charges for one order. `dp_applies=False` suppresses the DP charge on a delivery
    sell when the same scrip was already sold from demat earlier that day."""
    _check_order(qty, price)
    turnover = Decimal(qty) * price

    def r(v: Decimal) -> Decimal:
        return _round(v, schedule)

    b = schedule.brokerage
    brokerage = r(max(b.min_per_order, min(b.max_per_order, turnover * b.pct)))

    if trade_type is TradeType.DELIVERY:
        stt_rate = schedule.stt.delivery_buy if side is Side.BUY else schedule.stt.delivery_sell
    else:
        stt_rate = ZERO if side is Side.BUY else schedule.stt.intraday_sell
    stt = r(turnover * stt_rate)

    # IPFT (investor protection fund) is an exchange levy; folded into the exchange line.
    exchange_txn = r(turnover * (schedule.exchange_txn_pct + schedule.ipft_pct))
    sebi_fee = r(turnover * schedule.sebi_fee_pct)

    if side is Side.BUY:
        stamp_rate = (
            schedule.stamp_duty.delivery_buy
            if trade_type is TradeType.DELIVERY
            else schedule.stamp_duty.intraday_buy
        )
        stamp_duty = r(turnover * stamp_rate)
    else:
        stamp_duty = r(ZERO)

    gst_base = brokerage + sebi_fee
    if schedule.gst_on_exchange_txn:
        gst_base += exchange_txn
    gst = r(gst_base * schedule.gst_pct)

    if side is Side.SELL and trade_type is TradeType.DELIVERY and dp_applies:
        dp = schedule.dp_charge.amount
        if schedule.dp_charge.gst_applies:
            dp = dp * (1 + schedule.gst_pct)
        dp_charge = r(dp)
    else:
        dp_charge = r(ZERO)

    total = brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst + dp_charge
    return LegCost(
        side=side,
        trade_type=trade_type,
        qty=qty,
        price=price,
        turnover=turnover,
        brokerage=brokerage,
        stt=stt,
        exchange_txn=exchange_txn,
        sebi_fee=sebi_fee,
        stamp_duty=stamp_duty,
        gst=gst,
        dp_charge=dp_charge,
        total=total,
    )


def round_trip_cost(
    schedule: ChargeSchedule,
    *,
    trade_type: TradeType,
    qty: int,
    entry_price: Decimal,
    exit_price: Decimal,
    dp_applies: bool = True,
) -> RoundTripCost:
    """Charges for a long round trip: buy at `entry_price`, sell at `exit_price`."""
    entry = leg_cost(schedule, side=Side.BUY, trade_type=trade_type, qty=qty, price=entry_price)
    exit_ = leg_cost(
        schedule,
        side=Side.SELL,
        trade_type=trade_type,
        qty=qty,
        price=exit_price,
        dp_applies=dp_applies,
    )
    total = entry.total + exit_.total
    return RoundTripCost(
        entry=entry,
        exit=exit_,
        total=total,
        pct_of_entry_value=total / entry.turnover,
    )


def net_pnl(
    schedule: ChargeSchedule,
    *,
    trade_type: TradeType,
    qty: int,
    entry_price: Decimal,
    exit_price: Decimal,
    dp_applies: bool = True,
) -> Decimal:
    """Rupee P&L of a long round trip after all charges."""
    rt = round_trip_cost(
        schedule,
        trade_type=trade_type,
        qty=qty,
        entry_price=entry_price,
        exit_price=exit_price,
        dp_applies=dp_applies,
    )
    return (exit_price - entry_price) * qty - rt.total


def _gross_risk(qty: int, entry: Decimal, stop: Decimal) -> Decimal:
    risk = (entry - stop) * qty
    if risk <= 0:
        raise ValueError(f"stop {stop} must be below entry {entry} for a long")
    return risk


def net_r_multiple(
    schedule: ChargeSchedule,
    *,
    trade_type: TradeType,
    qty: int,
    entry: Decimal,
    stop: Decimal,
    exit_price: Decimal,
) -> Decimal:
    """Realised R after costs, where 1R = (entry - stop) * qty. A stop-out is below -1."""
    risk = _gross_risk(qty, entry, stop)
    pnl = net_pnl(
        schedule, trade_type=trade_type, qty=qty, entry_price=entry, exit_price=exit_price
    )
    return pnl / risk


def net_reward_risk(
    schedule: ChargeSchedule,
    *,
    trade_type: TradeType,
    qty: int,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
) -> Decimal:
    """Net reward:risk to `target` - the number the 2R rule in PLAN.md 1.2 is judged on.

    (gross reward - costs if the target is hit) / (gross risk + costs if the stop is hit)
    """
    _gross_risk(qty, entry, stop)
    if target <= entry:
        raise ValueError(f"target {target} must be above entry {entry} for a long")
    reward_net = net_pnl(
        schedule, trade_type=trade_type, qty=qty, entry_price=entry, exit_price=target
    )
    loss_net = -net_pnl(
        schedule, trade_type=trade_type, qty=qty, entry_price=entry, exit_price=stop
    )
    return reward_net / loss_net
