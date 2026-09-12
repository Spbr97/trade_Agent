"""CryptoCostModel (M13 Phase 4). Unlike EquityCostModel this is NOT a thin wrapper - the
maths are new - so it needs its own worked examples, hand-computed independently (same
discipline as tests/golden/rate_card_examples.yaml), plus property tests analogous to
tests/unit/test_costs_properties.py.

Numbers: 0.2% maker/taker (base tier), 1% TDS on the sell leg, 18% GST on the fee only -
see CryptoChargeSchedule's docstring for sources; this is a real, sourced rate card, but
not a real bill the way the 5 NSE ledger notes are - there is no crypto trade history to
verify against yet.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from tradedesk.config.models import CryptoChargeSchedule
from tradedesk.markets.costs import CostModel, CryptoCostModel
from tradedesk.models import Side, TradeType

qtys = st.integers(min_value=1, max_value=1000)
prices = st.decimals(min_value=D("1"), max_value=D("10000000"), places=2)


@pytest.fixture(scope="session")
def crypto_schedule() -> CryptoChargeSchedule:
    return CryptoChargeSchedule()


def test_crypto_cost_model_satisfies_the_protocol(crypto_schedule: CryptoChargeSchedule) -> None:
    assert isinstance(CryptoCostModel(crypto_schedule), CostModel)


# ------------------------------------------------------------- worked example


def test_worked_example_buy_77000_sell_78500(crypto_schedule: CryptoChargeSchedule) -> None:
    """Hand-computed independently (see the conversation, not this file) - qty 1 at
    round prices keeps the arithmetic checkable by hand:
      buy:  turnover 77,000.00; fee 0.2% = 154.00; GST 18% of fee = 27.72; total 181.72
      sell: turnover 78,500.00; fee 0.2% = 157.00; GST = 28.26; TDS 1% = 785.00;
            total 970.26
      round trip: 181.72 + 970.26 = 1,151.98
    """
    model = CryptoCostModel(crypto_schedule)
    buy = model.leg_cost(side=Side.BUY, trade_type=TradeType.DELIVERY, qty=1, price=D("77000.00"))
    assert buy.brokerage == D("154.00")
    assert buy.gst == D("27.72")
    assert buy.stt == D("0.00")  # no TDS on the buy leg
    assert buy.total == D("181.72")

    sell = model.leg_cost(side=Side.SELL, trade_type=TradeType.DELIVERY, qty=1, price=D("78500.00"))
    assert sell.brokerage == D("157.00")
    assert sell.gst == D("28.26")
    assert sell.stt == D("785.00")  # TDS lives in the stt slot - see the class docstring
    assert sell.total == D("970.26")

    rt = model.round_trip_cost(
        trade_type=TradeType.DELIVERY, qty=1, entry_price=D("77000.00"), exit_price=D("78500.00")
    )
    assert rt.total == D("1151.98")

    # every other market-specific line is always zero
    for leg in (buy, sell):
        assert leg.exchange_txn == leg.ipft == leg.sebi_fee == leg.stamp_duty == leg.dp_charge == 0


def test_trade_type_is_ignored_unlike_nse(crypto_schedule: CryptoChargeSchedule) -> None:
    """TDS/fees apply the same regardless of hold duration - no INTRADAY/DELIVERY split."""
    model = CryptoCostModel(crypto_schedule)
    a = model.leg_cost(side=Side.SELL, trade_type=TradeType.INTRADAY, qty=1, price=D("1000"))
    b = model.leg_cost(side=Side.SELL, trade_type=TradeType.DELIVERY, qty=1, price=D("1000"))
    # trade_type itself legitimately differs (it's carried through, not recomputed) -
    # every actual cost line must not.
    assert (a.brokerage, a.gst, a.stt, a.total) == (b.brokerage, b.gst, b.stt, b.total)


def test_net_reward_risk_is_worse_than_gross_because_of_tds(
    crypto_schedule: CryptoChargeSchedule,
) -> None:
    """The plan's stated risk: 1% TDS on every sell should visibly hurt a short-hold
    setup's net R:R relative to its gross reward:risk."""
    model = CryptoCostModel(crypto_schedule)
    entry, stop, target = D("1000"), D("980"), D("1040")  # gross 2R
    gross_rr = (target - entry) / (entry - stop)
    net_rr = model.net_reward_risk(
        trade_type=TradeType.DELIVERY, qty=100, entry=entry, stop=stop, target=target
    )
    assert net_rr < gross_rr
    assert gross_rr == D("2")
    assert net_rr < D("1.8")  # TDS alone is already ~1% of a much smaller risk base


# --------------------------------------------------------------- properties


@given(qty=qtys, price=prices, side=st.sampled_from(list(Side)))
@hyp_settings(max_examples=200)
def test_total_is_sum_of_lines_and_non_negative(
    crypto_schedule: CryptoChargeSchedule, qty: int, price: D, side: Side
) -> None:
    model = CryptoCostModel(crypto_schedule)
    leg = model.leg_cost(side=side, trade_type=TradeType.DELIVERY, qty=qty, price=price)
    lines = [leg.brokerage, leg.stt, leg.gst, leg.exchange_txn, leg.ipft, leg.sebi_fee,
             leg.stamp_duty, leg.dp_charge]  # fmt: skip
    assert all(x >= 0 for x in lines)
    assert leg.total == sum(lines)


@given(qty=qtys, price=prices)
@hyp_settings(max_examples=200)
def test_sell_always_costs_at_least_as_much_as_buy_because_of_tds(
    crypto_schedule: CryptoChargeSchedule, qty: int, price: D
) -> None:
    model = CryptoCostModel(crypto_schedule)
    buy = model.leg_cost(side=Side.BUY, trade_type=TradeType.DELIVERY, qty=qty, price=price)
    sell = model.leg_cost(side=Side.SELL, trade_type=TradeType.DELIVERY, qty=qty, price=price)
    assert sell.total > buy.total  # same turnover, sell also pays TDS


@given(qty=qtys, price=prices)
@hyp_settings(max_examples=100)
def test_monotone_in_qty_and_price(
    crypto_schedule: CryptoChargeSchedule, qty: int, price: D
) -> None:
    model = CryptoCostModel(crypto_schedule)
    base = model.leg_cost(side=Side.SELL, trade_type=TradeType.DELIVERY, qty=qty, price=price)
    more_qty = model.leg_cost(
        side=Side.SELL, trade_type=TradeType.DELIVERY, qty=qty + 1, price=price
    )
    more_price = model.leg_cost(
        side=Side.SELL, trade_type=TradeType.DELIVERY, qty=qty, price=price + 1
    )
    assert more_qty.total >= base.total
    assert more_price.total >= base.total


def test_rejects_non_positive_qty_or_price(crypto_schedule: CryptoChargeSchedule) -> None:
    model = CryptoCostModel(crypto_schedule)
    with pytest.raises(ValueError, match="qty"):
        model.leg_cost(side=Side.BUY, trade_type=TradeType.DELIVERY, qty=0, price=D("1"))
    with pytest.raises(ValueError, match="price"):
        model.leg_cost(side=Side.BUY, trade_type=TradeType.DELIVERY, qty=1, price=D("0"))
