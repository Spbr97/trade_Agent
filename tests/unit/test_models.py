"""tradedesk.models: shared enums and the price_decimal helper.

Regression for a real crash (M13, full-history crypto backtest, 2026-09-12):
`Decimal(str(round(price, 2)))` truncated meme-coin prices like SHIBINR's ~Rs 0.0004 to
0.00, and CryptoCostModel.leg_cost() then rejected it outright ("price must be positive,
got 0.0"). price_decimal rounds to 8 places instead so sub-paisa prices survive.
"""

from __future__ import annotations

from decimal import Decimal

from tradedesk.markets.costs import CryptoCostModel
from tradedesk.models import Side, TradeType, price_decimal


def test_price_decimal_keeps_sub_paisa_prices_nonzero() -> None:
    assert price_decimal(0.0004) == Decimal("0.0004")
    assert price_decimal(0.00023) == Decimal("0.00023")


def test_price_decimal_matches_round_to_2_for_nse_prices() -> None:
    # NSE prices are exact at 2 decimals, so the extra precision changes nothing there.
    assert price_decimal(842.005) == Decimal(str(round(842.005, 8)))
    assert float(price_decimal(842.0)) == 842.0


def test_leg_cost_no_longer_crashes_on_a_meme_coin_price() -> None:
    from tradedesk.config.models import CryptoChargeSchedule

    model = CryptoCostModel(CryptoChargeSchedule())
    price = price_decimal(0.0004)
    leg = model.leg_cost(side=Side.BUY, trade_type=TradeType.DELIVERY, qty=1_000_000, price=price)
    assert leg.total > 0
