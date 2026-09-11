"""nse_market(settings) must reproduce today's NSE configuration exactly - it is a bundle,
not new behaviour (see market.py's module docstring)."""

from __future__ import annotations

import dataclasses

import pytest

from tradedesk.config.models import Settings
from tradedesk.markets import EquityCostModel, Market, nse_market


def test_nse_market_wraps_settings_unchanged(settings: Settings) -> None:
    m = nse_market(settings)
    assert isinstance(m, Market)
    assert m.name == "nse"
    assert m.code_prefix == "NSE_"
    assert m.benchmark_name == settings.universe.benchmark
    assert isinstance(m.costs, EquityCostModel)
    assert m.costs.schedule is settings.risk.costs  # same object, not a copy


def test_nse_market_universe_rules_match_config(settings: Settings) -> None:
    m = nse_market(settings)
    assert m.universe_rules.min_avg_turnover_inr == float(
        settings.universe.min_avg_daily_turnover_inr
    )
    assert m.universe_rules.min_price == float(settings.universe.min_price)


def test_nse_market_session_rules_match_the_documented_nse_hours(settings: Settings) -> None:
    m = nse_market(settings)
    assert m.session_rules.session_open.isoformat() == "09:15:00"
    assert m.session_rules.session_close.isoformat() == "15:30:00"


def test_market_is_frozen(settings: Settings) -> None:
    m = nse_market(settings)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.name = "crypto"  # type: ignore[misc]
