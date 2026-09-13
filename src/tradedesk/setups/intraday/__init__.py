"""Intraday setups (SDD section 7). One module per setup, same one-module-per-setup
convention as `tradedesk.setups`, implementing `IntradaySetup` instead of `Setup`."""

from tradedesk.engine.intraday_signals import IntradaySetupKind
from tradedesk.setups.intraday.base import IntradaySetup, IntradaySetupContext
from tradedesk.setups.intraday.vwap_reclaim import VwapReclaim

INTRADAY_REGISTRY: dict[IntradaySetupKind, IntradaySetup] = {
    IntradaySetupKind.VWAP_RECLAIM: VwapReclaim(),
}

__all__ = ["INTRADAY_REGISTRY", "IntradaySetup", "IntradaySetupContext", "IntradaySetupKind"]
