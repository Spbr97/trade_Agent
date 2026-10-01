"""Setups (PLAN.md 6.5). One module per setup implementing the Setup protocol."""

from tradedesk.engine.signals import SetupKind
from tradedesk.setups.base import Setup, SetupContext
from tradedesk.setups.base_breakout import BaseBreakout
from tradedesk.setups.nr7_breakout import Nr7Breakout
from tradedesk.setups.r_adx_thrust_tsmom_up_trend_trail import RAdxThrustTsmomUpTrendTrail
from tradedesk.setups.r_donchian55_rsi_momentum_trend_trail import RDonchian55RsiMomentumTrendTrail
from tradedesk.setups.r_high52_break_strong_trend_trend_trail import RHigh52BreakStrongTrendTrendTrail
from tradedesk.setups.r_tsmom20_strong_trend_trend_trail import RTsmom20StrongTrendTrendTrail
from tradedesk.setups.r_tsmom252_rsi_momentum_trend_trail import RTsmom252RsiMomentumTrendTrail
from tradedesk.setups.trend_pullback import TrendPullback

REGISTRY: dict[SetupKind, Setup] = {
    SetupKind.BASE_BREAKOUT: BaseBreakout(),
    SetupKind.TREND_PULLBACK: TrendPullback(),
    SetupKind.NR7_BREAKOUT: Nr7Breakout(),
    SetupKind.R_DONCHIAN55_RSI_MOMENTUM_TREND_TRAIL: RDonchian55RsiMomentumTrendTrail(),
    SetupKind.R_TSMOM252_RSI_MOMENTUM_TREND_TRAIL: RTsmom252RsiMomentumTrendTrail(),
    SetupKind.R_HIGH52_BREAK_STRONG_TREND_TREND_TRAIL: RHigh52BreakStrongTrendTrendTrail(),
    SetupKind.R_TSMOM20_STRONG_TREND_TREND_TRAIL: RTsmom20StrongTrendTrendTrail(),
    SetupKind.R_ADX_THRUST_TSMOM_UP_TREND_TRAIL: RAdxThrustTsmomUpTrendTrail(),
}

__all__ = ["REGISTRY", "Setup", "SetupContext", "SetupKind"]
