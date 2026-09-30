"""After-tax view of a crypto trade's R, used to keep only patterns that survive Indian VDA tax.

Crypto gains are taxed at a flat 30% (Section 115BBH) with no set-off of losses, so every
winning transaction is taxed on its own gain while losers are never refunded. Trading fees
are not deductible either, so the tax base is the trade's GROSS gain. The 1% Section 194S
TDS deducted on the sell leg is a prepayment: credited against that tax or refunded, so it is
added back to the pre-tax result before the tax is subtracted.

`HarnessTrade` carries only `gross_r` and `net_r` per trade, so the TDS part of the cost
(`gross_r - net_r`) is taken as its share of the round-trip charges. The share deliberately
puts slippage in the denominator, which credits back LESS TDS - the conservative side.

This is the author's reading of the tax law, not tax advice; confirm it before relying on it."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

VDA_TAX_RATE = 0.30


class _Trade(Protocol):
    gross_r: float
    net_r: float


def tds_share_of_costs(
    *, maker_taker_pct: float, tds_pct: float, gst_pct: float, slippage_pct: float
) -> float:
    fees = 2 * maker_taker_pct * (1 + gst_pct)
    return tds_pct / (fees + tds_pct + 2 * slippage_pct)


def after_tax_r(trade: _Trade, tds_share: float, tax_rate: float = VDA_TAX_RATE) -> float:
    """R kept after fees, with TDS credited back and 30% tax on a winning trade's gross gain."""
    cost_r = trade.gross_r - trade.net_r
    return trade.net_r + tds_share * cost_r - tax_rate * max(trade.gross_r, 0.0)


def mean_after_tax_r(
    trades: Iterable[_Trade], tds_share: float, tax_rate: float = VDA_TAX_RATE
) -> float | None:
    values = [after_tax_r(t, tds_share, tax_rate) for t in trades]
    return sum(values) / len(values) if values else None
