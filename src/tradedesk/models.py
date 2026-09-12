"""Shared enums and small value types used across tradedesk.

Larger domain models (Candle, Signal, Position, TradeCard, ...) are added by the
milestones that introduce them.
"""

from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TradeType(StrEnum):
    """How the exchange and broker charge the trade.

    A position bought and sold on the same day is INTRADAY; anything carried
    overnight is DELIVERY. Callers decide; the cost code never infers it.
    """

    INTRADAY = "intraday"
    DELIVERY = "delivery"


def price_decimal(x: float) -> Decimal:
    """A float price -> Decimal, rounded to 8 places rather than 2.

    Found as a real bug (M13, full-history crypto backtest, 2026-09-12): rounding to 2
    decimals - fine for NSE, which never prices below about a rupee - truncates a
    meme-coin price like SHIBINR's ~Rs 0.0004 to 0.00, and risk/costs.py's leg_cost()
    then rejects it outright ("price must be positive, got 0.0"), crashing the backtest
    the moment such a token's position gets settled. 8 places represents crypto's real
    range (observed down to ~Rs 0.0002) while changing nothing for NSE, whose prices
    are exact at 2 decimals anyway - the extra precision just absorbs float noise from
    upstream indicator math the same way `round(x, 2)` did.
    """
    return Decimal(str(round(x, 8)))


def qty_decimal(x: float) -> Decimal:
    """A quantity -> Decimal, exactly, for mixing with Decimal money.

    `Decimal(qty)` was fine while every quantity was a whole number of shares, but a
    fractional crypto quantity is a float, and `Decimal(0.0032)` expands the binary float
    into 0.00319999999999999996... - while `Decimal * float` raises outright. Going via
    str() gives the decimal number that was actually meant. NSE is unaffected: str(20) is
    "20", so `Decimal("20")` is the same value `Decimal(20)` always produced.
    """
    return Decimal(str(x))
