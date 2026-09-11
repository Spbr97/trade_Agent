"""Table tests for the cost calculator. Expected figures are hand-computed from INDmoney's
published equity rates (see conftest.default_schedule) with per-line half-up rounding
to the paisa."""

from decimal import Decimal as D

import pytest

from tradedesk.config.models import ChargeSchedule
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import (
    leg_cost,
    net_pnl,
    net_r_multiple,
    net_reward_risk,
    round_trip_cost,
)

DLV = TradeType.DELIVERY
INT = TradeType.INTRADAY


def test_delivery_buy_leg_lines(default_schedule: ChargeSchedule) -> None:
    leg = leg_cost(default_schedule, side=Side.BUY, trade_type=DLV, qty=100, price=D(1000))
    assert leg.turnover == D("100000")
    assert leg.brokerage == D("5.00")  # 0.1% = 100 > Rs 5 cap
    assert leg.stt == D("100.00")
    assert leg.exchange_txn == D("3.07")  # 0.00307%
    assert leg.ipft == D("0.10") and leg.sebi_fee == D("0.10")  # 0.0001% each
    assert leg.stamp_duty == D("15.00")
    assert leg.gst == D("1.47")  # 18% of (5 + 3.07 + 0.10): IPFT, STT and stamp excluded
    assert leg.dp_charge == D("0.00")
    assert leg.total == D("124.74")


def test_delivery_sell_leg_lines(default_schedule: ChargeSchedule) -> None:
    leg = leg_cost(default_schedule, side=Side.SELL, trade_type=DLV, qty=100, price=D(1000))
    assert leg.stt == D("100.00")
    assert leg.stamp_duty == D("0.00")  # buy side only
    assert leg.dp_charge == D("21.83")  # 18.5 + 18% GST
    assert leg.total == D("131.57")


def test_intraday_legs(default_schedule: ChargeSchedule) -> None:
    buy = leg_cost(default_schedule, side=Side.BUY, trade_type=INT, qty=100, price=D(1000))
    sell = leg_cost(default_schedule, side=Side.SELL, trade_type=INT, qty=100, price=D(1000))
    assert buy.stt == D("0.00")  # intraday STT is on the sell only
    assert sell.stt == D("25.00")
    assert buy.stamp_duty == D("3.00")
    assert sell.stamp_duty == D("0.00")
    assert buy.dp_charge == D("0.00") and sell.dp_charge == D("0.00")
    assert buy.total == D("12.74")
    assert sell.total == D("34.74")


@pytest.mark.parametrize(
    ("qty", "price", "expected"),
    [
        (10, D(100), D("2.00")),  # 0.1% of 1,000 = 1.00 -> floor Rs 2
        (10, D(300), D("3.00")),  # 0.1% of 3,000 = 3.00 -> between floor and cap
        (10, D(500), D("5.00")),  # 0.1% of 5,000 = 5.00 -> exactly the cap
        (20, D(842), D("5.00")),  # 0.1% of 16,840 = 16.84 -> cap Rs 5
    ],
)
def test_brokerage_tiers(default_schedule: ChargeSchedule, qty: int, price: D, expected: D) -> None:
    leg = leg_cost(default_schedule, side=Side.BUY, trade_type=INT, qty=qty, price=price)
    assert leg.brokerage == expected


def test_dp_charge_can_be_suppressed_for_second_partial(default_schedule: ChargeSchedule) -> None:
    leg = leg_cost(
        default_schedule, side=Side.SELL, trade_type=DLV, qty=10, price=D(100), dp_applies=False
    )
    assert leg.dp_charge == D("0.00")


def test_dp_gst_flag(default_schedule: ChargeSchedule) -> None:
    sched = default_schedule.model_copy(
        update={"dp_charge": default_schedule.dp_charge.model_copy(update={"gst_applies": False})}
    )
    leg = leg_cost(sched, side=Side.SELL, trade_type=DLV, qty=1, price=D(100))
    assert leg.dp_charge == D("18.50")


def test_statutory_rounding_to_rupee_per_trade(default_schedule: ChargeSchedule) -> None:
    # Ledger-verified: ADANIPOWER 5 @ 172.95 delivery buy cost Rs 3.40 in total.
    leg = leg_cost(default_schedule, side=Side.BUY, trade_type=DLV, qty=5, price=D("172.95"))
    assert leg.stt == D("1.00")  # 0.86 -> 1
    assert leg.stamp_duty == D("0.00")  # 0.13 -> 0
    assert leg.brokerage == D("2.00")  # Rs 2 minimum on a Rs 865 order
    assert leg.total == D("3.40")
    paise = default_schedule.model_copy(update={"statutory_rounding": "paise"})
    assert leg_cost(paise, side=Side.BUY, trade_type=DLV, qty=5, price=D("172.95")).stt == D("0.86")


def test_gst_base_can_exclude_exchange_txn(default_schedule: ChargeSchedule) -> None:
    # IND Pricing lists GST on brokerage + SEBI (+ DP) but not on exchange charges.
    sched = default_schedule.model_copy(update={"gst_on_exchange_txn": False})
    leg = leg_cost(sched, side=Side.BUY, trade_type=DLV, qty=100, price=D(1000))
    assert leg.gst == D("0.92")  # 18% of (5 + 0.10) = 0.918


def test_one_lakh_delivery_round_trip(default_schedule: ChargeSchedule) -> None:
    rt = round_trip_cost(
        default_schedule, trade_type=DLV, qty=100, entry_price=D(1000), exit_price=D(1000)
    )
    assert rt.total == D("256.31")
    assert D("0.0025") < rt.pct_of_entry_value < D("0.0027")


def test_trade_card_at_indmoney_rates(default_schedule: ChargeSchedule) -> None:
    # PLAN.md 7 trade card (qty 20, entry 842, stop 818, T1 890, T2 914) at real rates.
    rt = round_trip_cost(
        default_schedule, trade_type=DLV, qty=20, entry_price=D(842), exit_price=D(890)
    )
    assert rt.entry.stt == D("17.00") and rt.entry.stamp_duty == D("3.00")  # rupee rounding
    assert rt.entry.total == D("26.56")
    assert rt.exit.total == D("46.42")
    assert rt.total == D("72.98")
    rr_t1 = net_reward_risk(
        default_schedule, trade_type=DLV, qty=20, entry=D(842), stop=D(818), target=D(890)
    )
    rr_t2 = net_reward_risk(
        default_schedule, trade_type=DLV, qty=20, entry=D(842), stop=D(818), target=D(914)
    )
    assert rr_t1.quantize(D("0.01")) == D("1.61")
    assert rr_t2.quantize(D("0.01")) == D("2.48")


def test_spec_anchor_reproduces_plan_numbers(flat20_schedule: ChargeSchedule) -> None:
    # PLAN.md was written assuming Rs 20/order: "~Rs 110 round trip, net R:R 1.4 at T1,
    # 2.3 at T2", and "about 0.3% on a Rs 1,00,000 delivery trade". Proves the convention
    # net R:R = (reward - costs) / (risk + costs at stop) is what the plan means.
    lakh = round_trip_cost(
        flat20_schedule, trade_type=DLV, qty=100, entry_price=D(1000), exit_price=D(1000)
    )
    assert D("0.0028") < lakh.pct_of_entry_value < D("0.0030")
    rt = round_trip_cost(
        flat20_schedule, trade_type=DLV, qty=20, entry_price=D(842), exit_price=D(890)
    )
    assert D("105") <= rt.total <= D("110")
    rr_t1 = net_reward_risk(
        flat20_schedule, trade_type=DLV, qty=20, entry=D(842), stop=D(818), target=D(890)
    )
    rr_t2 = net_reward_risk(
        flat20_schedule, trade_type=DLV, qty=20, entry=D(842), stop=D(818), target=D(914)
    )
    assert D("1.4") <= rr_t1 <= D("1.5")
    assert D("2.2") <= rr_t2 <= D("2.35")


def test_net_pnl_and_r_multiple(default_schedule: ChargeSchedule) -> None:
    pnl = net_pnl(default_schedule, trade_type=DLV, qty=20, entry_price=D(842), exit_price=D(890))
    assert pnl == D("960") - D("72.98")
    r = net_r_multiple(
        default_schedule, trade_type=DLV, qty=20, entry=D(842), stop=D(818), exit_price=D(818)
    )
    assert r < D("-1")  # a stop-out loses more than 1R once costs are paid


def test_rejects_bad_inputs(default_schedule: ChargeSchedule) -> None:
    with pytest.raises(ValueError):
        leg_cost(default_schedule, side=Side.BUY, trade_type=DLV, qty=0, price=D(10))
    with pytest.raises(ValueError):
        leg_cost(default_schedule, side=Side.BUY, trade_type=DLV, qty=1, price=D(-1))
    with pytest.raises(ValueError):
        net_reward_risk(
            default_schedule, trade_type=DLV, qty=1, entry=D(100), stop=D(100), target=D(110)
        )
