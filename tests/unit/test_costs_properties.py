from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from tradedesk.config.models import ChargeSchedule
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import leg_cost, net_reward_risk, round_trip_cost

qtys = st.integers(min_value=1, max_value=5000)
prices = st.decimals(min_value=D("1"), max_value=D("50000"), places=2)
trade_types = st.sampled_from(list(TradeType))
sides = st.sampled_from(list(Side))


@given(qty=qtys, price=prices, trade_type=trade_types, side=sides)
@settings(max_examples=300)
def test_total_is_sum_of_lines_and_non_negative(
    default_schedule: ChargeSchedule, qty: int, price: D, trade_type: TradeType, side: Side
) -> None:
    leg = leg_cost(default_schedule, side=side, trade_type=trade_type, qty=qty, price=price)
    lines = [
        leg.brokerage,
        leg.stt,
        leg.exchange_txn,
        leg.sebi_fee,
        leg.stamp_duty,
        leg.gst,
        leg.dp_charge,
    ]
    assert all(x >= 0 for x in lines)
    assert leg.total == sum(lines)
    assert leg.total == leg.total.quantize(D("0.01"))


@given(qty=qtys, price=prices, trade_type=trade_types, side=sides)
@settings(max_examples=200)
def test_monotone_in_qty_and_price(
    default_schedule: ChargeSchedule, qty: int, price: D, trade_type: TradeType, side: Side
) -> None:
    base = leg_cost(default_schedule, side=side, trade_type=trade_type, qty=qty, price=price)
    more_qty = leg_cost(
        default_schedule, side=side, trade_type=trade_type, qty=qty + 1, price=price
    )
    more_price = leg_cost(
        default_schedule, side=side, trade_type=trade_type, qty=qty, price=price + 1
    )
    assert more_qty.total >= base.total
    assert more_price.total >= base.total


@given(qty=qtys, entry=prices, exit_price=prices)
@settings(max_examples=200)
def test_delivery_costs_at_least_intraday(
    default_schedule: ChargeSchedule, qty: int, entry: D, exit_price: D
) -> None:
    dlv = round_trip_cost(
        default_schedule,
        trade_type=TradeType.DELIVERY,
        qty=qty,
        entry_price=entry,
        exit_price=exit_price,
    )
    intr = round_trip_cost(
        default_schedule,
        trade_type=TradeType.INTRADAY,
        qty=qty,
        entry_price=entry,
        exit_price=exit_price,
    )
    assert dlv.total >= intr.total
    floor = default_schedule.brokerage.min_per_order * 2 * (1 + default_schedule.gst_pct)
    assert intr.total >= floor.quantize(D("0.01")) - D("0.02")  # per-line rounding slack


@given(
    qty=qtys,
    entry=prices,
    stop_dist=st.decimals(min_value=D("0.05"), max_value=D("100"), places=2),
    reward_mult=st.decimals(min_value=D("0.5"), max_value=D("5"), places=2),
)
@settings(max_examples=200)
def test_net_rr_strictly_below_gross_rr(
    default_schedule: ChargeSchedule, qty: int, entry: D, stop_dist: D, reward_mult: D
) -> None:
    stop = entry - stop_dist
    if stop <= 0:
        return
    target = entry + stop_dist * reward_mult
    net = net_reward_risk(
        default_schedule,
        trade_type=TradeType.DELIVERY,
        qty=qty,
        entry=entry,
        stop=stop,
        target=target,
    )
    assert net < reward_mult
