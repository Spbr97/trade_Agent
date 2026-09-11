"""Setups (PLAN.md 6.5). One module per setup implementing the Setup protocol."""

from tradedesk.engine.signals import SetupKind
from tradedesk.setups.base import Setup, SetupContext
from tradedesk.setups.base_breakout import BaseBreakout
from tradedesk.setups.nr7_breakout import Nr7Breakout
from tradedesk.setups.trend_pullback import TrendPullback

REGISTRY: dict[SetupKind, Setup] = {
    SetupKind.BASE_BREAKOUT: BaseBreakout(),
    SetupKind.TREND_PULLBACK: TrendPullback(),
    SetupKind.NR7_BREAKOUT: Nr7Breakout(),
}

__all__ = ["REGISTRY", "Setup", "SetupContext", "SetupKind"]
