"""amount_test: only the rupee size may change, and the charges must follow it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tradedesk_lab import amount_test as at
from tradedesk_lab import intraday_playbook as ip


class FlatCost:
    """Rs 10 an order, the shape that makes bigger trades cheaper."""

    def round_trip_cost(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(total=20.0)


def _fade(entry: float = 100.0, exit_px: float = 99.0, net: float = 0.0, excess: float = 0.002):
    return {"entry": entry, "exit": exit_px, "net": net, "excess": excess, "mae": 0.05,
            "day": "2026-11-04"}  # fmt: skip


def test_a_bigger_fade_pays_less_in_charges_and_keeps_the_same_move() -> None:
    small, = at.fade_at([_fade()], FlatCost(), 10_000)
    big, = at.fade_at([_fade()], FlatCost(), 100_000)
    assert small["net"] == pytest.approx(0.01 - 20 / 10_000)
    assert big["net"] == pytest.approx(0.01 - 20 / 100_000)
    assert big["rupees"] == pytest.approx(1000 - 20)


def test_the_fade_excess_moves_only_by_the_change_in_charges() -> None:
    row, = at.fade_at([_fade(net=0.008, excess=0.011)], FlatCost(), 100_000)
    assert row["excess"] == pytest.approx(0.011 + (0.01 - 20 / 100_000) - 0.008)


def test_the_worst_open_loss_scales_with_the_position() -> None:
    row, = at.fade_at([_fade()], FlatCost(), 100_000)
    assert row["worst_rupees"] == pytest.approx(-0.05 * 100_000)


def test_a_share_dearer_than_the_amount_is_left_out() -> None:
    assert at.fade_at([_fade(entry=12_000.0, exit_px=11_900.0)], FlatCost(), 10_000) == []


def _trade(entry: float = 100.0, stop: float = 99.0, gross_r: float = 1.0) -> ip.Trade:
    return ip.Trade("orb15", "long", "X", "2026-01-01", (1, 2), 1, entry, stop, gross_r)


def test_a_rule_trade_is_repriced_in_r_from_its_real_stop() -> None:
    t, = at.reprice([_trade()], FlatCost(), 10_000)  # 100 shares, Rs 100 of risk
    assert t.net_r == pytest.approx(1.0 - 20 / 100)
    assert t.rupees == pytest.approx(100 - 20)
    assert t.gross_r == 1.0


def test_bigger_trades_keep_more_of_the_same_gross_r() -> None:
    small = at.reprice([_trade()], FlatCost(), 10_000)[0].net_r
    big = at.reprice([_trade()], FlatCost(), 100_000)[0].net_r
    assert big > small


def test_a_rule_trade_dearer_than_the_amount_is_left_out() -> None:
    assert at.reprice([_trade(entry=20_000.0, stop=19_900.0)], FlatCost(), 10_000) == []


def test_the_market_aligned_filter_matches_the_playbooks() -> None:
    long_up = _trade()
    long_up.market_move = 0.01
    assert at._in_variant(long_up, "market_aligned")
    long_up.market_move = -0.01
    assert not at._in_variant(long_up, "market_aligned")
    assert at._in_variant(long_up, "all")
