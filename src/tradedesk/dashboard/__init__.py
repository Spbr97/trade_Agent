"""Localhost dashboard (M8): FastAPI + server-sent events over DashboardState."""

from tradedesk.dashboard.app import create_app, serve
from tradedesk.dashboard.state import DashboardState

__all__ = ["DashboardState", "create_app", "serve"]
