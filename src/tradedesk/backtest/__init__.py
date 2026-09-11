"""Event-driven backtester (M5): gap-aware daily fills, portfolio limits in time order,
reports with a walk-forward split. Drives engine/engine.py with a historical clock."""

from tradedesk.backtest.reports import Report, build_report, walk_forward
from tradedesk.backtest.runner import BacktestConfig, BacktestResult, prepare_market, run_backtest

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "Report",
    "build_report",
    "prepare_market",
    "run_backtest",
    "walk_forward",
]
