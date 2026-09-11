"""Shared enums and small value types used across tradedesk.

Larger domain models (Candle, Signal, Position, TradeCard, ...) are added by the
milestones that introduce them.
"""

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
