"""Data layer (M3): DuckDB candle store, history loader, corporate actions, universe-by-date,
results calendar and the data-quality report."""

from tradedesk.data.candle_store import CandleStore, apply_adjustments
from tradedesk.data.models import (
    ActionKind,
    CorporateAction,
    IssueKind,
    QualityIssue,
    ResultsEvent,
)

__all__ = [
    "ActionKind",
    "CandleStore",
    "CorporateAction",
    "IssueKind",
    "QualityIssue",
    "ResultsEvent",
    "apply_adjustments",
]
