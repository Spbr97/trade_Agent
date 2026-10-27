"""Owner-approved 2026-10-28: momentum rule setups share one position's risk per evening."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

from tradedesk.scan.evening_scan import MOMENTUM_CLASS_REASON, cap_momentum_class, momentum_class


@dataclass(frozen=True)
class _Entry:
    setup: str
    risk_pct: float
    alertable: bool = True
    rejected_for: list[str] = field(default_factory=list)

    @property
    def signal(self) -> Any:
        return SimpleNamespace(setup=SimpleNamespace(value=self.setup))

    def model_copy(self, *, update: dict[str, Any]) -> _Entry:
        return replace(self, **update)


def test_momentum_rules_share_one_position_and_others_are_untouched() -> None:
    entries = [
        _Entry("r_tsmom60_rsi_momentum_trend_trail", 0.0025),  # full size: uses the budget
        _Entry("r_high52_break_strong_trend_trend_trail", 0.000625),  # over: rejected
        _Entry("r_rsi2_dip_above_ema200_revert_2r", 0.0025),  # reversion: not counted
        _Entry("base_breakout", 0.0025),  # hand-written setup: not counted
        _Entry("r_donchian20_strong_trend_trend_trail", 0.001, alertable=False),  # already out
    ]
    out = cap_momentum_class(entries, 0.0025)
    assert [e.alertable for e in out] == [True, False, True, True, False]
    assert MOMENTUM_CLASS_REASON in out[1].rejected_for[0]
    assert out[4].rejected_for == []


def test_quarter_size_calls_fit_four_to_a_full_position() -> None:
    entries = [_Entry(f"r_tsmom20_strong_trend_trend_trail_{i}", 0.000625) for i in range(5)]
    assert [e.alertable for e in cap_momentum_class(entries, 0.0025)] == [True] * 4 + [False]


def test_momentum_class_membership() -> None:
    assert momentum_class("r_ema20_bounce_strong_trend_trend_trail")
    assert not momentum_class("r_crsi2_dip_above_ema200_revert_1r")
    assert not momentum_class("trend_pullback")
