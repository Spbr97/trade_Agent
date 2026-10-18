from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tradedesk import profit_gate as pg
from tradedesk.broker.indstocks.models import IST

GOOD_HISTORY = {"passed": True, "net_r": 0.26, "n_trades": 1091, "win_rate": 0.48}


def _calls(values: list[float]) -> list[dict]:
    return [
        {"symbol": f"S{i}", "net_r": v, "won": v > 0, "resolved_at": f"2026-10-{1 + i % 28:02d}"}
        for i, v in enumerate(values)
    ]


def test_the_range_holds_the_true_mean_and_narrows_with_data() -> None:
    rng = random.Random(7)
    draws = [rng.choice([2.0, -1.0, 0.5, -1.0, 1.5]) for _ in range(2000)]  # mean 0.4
    lo_small, hi_small = pg.confidence_sequence(draws[:50], alpha=0.05)
    lo, hi = pg.confidence_sequence(draws, alpha=0.05)
    assert lo <= 0.4 <= hi and lo_small <= 0.4 <= hi_small
    assert hi - lo < hi_small - lo_small


def test_proven_history_alerts_at_quarter_size_until_live_proof() -> None:
    v = pg.decide("r_x", "bse", [], [], GOOD_HISTORY, alpha=0.01)
    assert v.status == "provisional" and v.size_factor == pg.PROVISIONAL_SIZE


def test_weak_or_thin_history_is_not_enough() -> None:
    thin = GOOD_HISTORY | {"n_trades": 300}
    weak = GOOD_HISTORY | {"net_r": 0.15}
    for hist in (thin, weak, None):
        assert pg.decide("r_x", "nse", [], [], hist, alpha=0.01).status == "collecting"
    assert pg.decide("r_x", "crypto", [], [], GOOD_HISTORY, alpha=0.01).status == "collecting"


def test_live_losses_stop_a_provisional_setup() -> None:
    losing = _calls([-1.1] * 60 + [0.5] * 10)
    v = pg.decide("r_x", "nse", losing, losing, GOOD_HISTORY, alpha=0.01)
    assert v.status == "stopped" and v.size_factor == 0.0


def test_a_long_losing_streak_stops_it_even_before_the_range_does() -> None:
    streak = _calls([2.0, 1.0] + [-1.0] * pg.STOP_LOSING_STREAK)
    v = pg.decide("r_x", "nse", streak, streak, GOOD_HISTORY, alpha=0.01)
    assert v.status == "stopped" and "in a row" in v.reason


def test_strong_live_profit_after_costs_is_tradeable_at_full_size() -> None:
    winners = _calls([1.0, 0.8, 1.2, 0.9] * 60)
    v = pg.decide("r_x", "nse", winners, winners, None, alpha=0.01)
    assert v.status == "tradeable" and v.size_factor == 1.0 and v.live_lower_r > 0.05


def test_live_losses_without_history_mean_no_edge_not_retired() -> None:
    losing = _calls([-1.0] * 80)
    assert pg.decide("nr7", "nse", losing, losing, None, alpha=0.01).status == "no_edge"


def test_only_live_rows_count_and_costs_are_taken_off(tmp_path: Path) -> None:
    rows = [
        {"source": "backfill", "setup": "a", "outcome": "target", "r_multiple": 2.0,
         "entry": 100.0, "stop": 95.0, "resolved_at": "2026-10-01"},
        {"source": "live", "setup": "a", "outcome": "target", "r_multiple": 2.0,
         "entry": 100.0, "stop": 95.0, "resolved_at": "2026-10-02"},
        {"source": "live", "setup": "a", "outcome": "stop", "net_r": -1.06,
         "resolved_at": "2026-10-03"},
        {"source": "live", "setup": "a", "outcome": "chased", "resolved_at": "2026-10-04"},
    ]  # fmt: skip
    path = tmp_path / pg.TRACKERS["nse"]
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    calls = pg.live_results("nse", root=tmp_path)["a"]
    assert [round(c["net_r"], 4) for c in calls] == [round(2.0 - 0.0039098 * 100 / 5, 4), -1.06]


def test_the_scan_fails_closed_on_a_missing_or_stale_status(tmp_path: Path) -> None:
    assert pg.alerting_setups("nse", root=tmp_path) == {}
    verdict = pg.decide("r_x", "nse", [], [], GOOD_HISTORY, alpha=0.01)
    pg.write([verdict], root=tmp_path)
    allowed = pg.alerting_setups("nse", root=tmp_path)
    assert (
        allowed["r_x"].size_factor == pg.PROVISIONAL_SIZE and allowed["r_x"].history_trades == 1091
    )
    later = datetime.now(IST) + pg.STATUS_MAX_AGE + timedelta(hours=1)
    assert pg.alerting_setups("nse", root=tmp_path, now=later) == {}


@pytest.mark.parametrize("value", [-50.0, 50.0])
def test_extreme_values_are_clipped_not_trusted(value: float) -> None:
    lo, hi = pg.confidence_sequence([value] * 10, alpha=0.05)
    assert pg.CLIP_R[0] <= lo <= hi <= pg.CLIP_R[1]
