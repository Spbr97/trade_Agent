"""Read-only CoinDCX adapter (M13 plan Phase 2). No auth, no order placement - see
rest.py's module docstring."""

from tradedesk.broker.coindcx.rest import CoinDcxClient, CoinDcxError

__all__ = ["CoinDcxClient", "CoinDcxError"]
