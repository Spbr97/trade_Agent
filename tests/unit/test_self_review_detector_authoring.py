"""self_review/detector_authoring.py: Phase 3's orchestration - verify_base() gate,
Registry bookkeeping, and the NewDetectorCode proposal that only appears on a real pass."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.candidates.mean_reversion_v1 import MeanReversionV1
from tradedesk_lab.harness.gauntlet import GauntletReport
from tradedesk_lab.harness.spec import KillCriteria
from tradedesk_lab.registry import Registry

from tradedesk.self_review import detector_authoring as da


class _FakeRoundTrip:
    def __init__(self, total):
        self.total = total


class _FakeCosts:
    def round_trip_cost(self, *, trade_type, qty, entry_price, exit_price):
        from decimal import Decimal

        notional = (Decimal(str(entry_price)) + Decimal(str(exit_price))) * Decimal(str(qty))
        return _FakeRoundTrip(total=notional * Decimal("0.0005"))


class _FakeRules:
    exclude_codes = ()


class _FakeMarket:
    costs = _FakeCosts()
    qty_step = 1.0
    universe_rules = _FakeRules()


def _price_series(n: int, *, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n, tz="Asia/Kolkata")
    closes = [100.0]
    for i in range(1, n):
        drift = -0.018 if i % 17 in (0, 1) else 0.0035
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.002)))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.normal(0.004, 0.001, n)))
    lows = closes * (1 - np.abs(rng.normal(0.004, 0.001, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [150_000] * n},
        index=idx,
    )


def test_author_and_validate_refuses_when_base_has_drifted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        da, "verify_base", lambda: {"unchanged": False, "files_checked": 5, "changed": ["x.py"]}
    )
    with pytest.raises(da.BaseDrifted, match="drifted"):
        da.author_and_validate(
            MeanReversionV1(), "nse", registry_path=tmp_path / "registry.sqlite"
        )


def test_author_and_validate_records_a_registry_row_either_way(
    monkeypatch, tmp_path: Path
) -> None:
    from tradedesk.engine.indicators import daily_features

    monkeypatch.setattr(
        da, "verify_base", lambda: {"unchanged": True, "files_checked": 5, "changed": []}
    )
    frames = {f"NSE_{i}": daily_features(_price_series(300, seed=i)) for i in range(6)}

    def _fake_load(db, *, max_codes=100, exclude=frozenset()):
        return frames, dict.fromkeys(frames, "X")

    monkeypatch.setattr(da, "load_real_daily_frames", _fake_load)
    monkeypatch.setattr(da, "load_config", lambda root: object())
    monkeypatch.setattr(da, "market_bundle", lambda market, settings: _FakeMarket())

    registry_path = tmp_path / "registry.sqlite"
    report, beats_random, experiment_id, candidate_id, artifact_path = da.author_and_validate(
        MeanReversionV1(),
        "nse",
        registry_path=registry_path,
        output_dir=tmp_path,
        kill_criteria=KillCriteria(
            min_net_expectancy_r=-10.0,
            min_sample_size=1,
            max_drawdown_pct=1.0,
            max_losing_streak=1000,
        ),
    )

    assert isinstance(report, GauntletReport)
    assert artifact_path.exists()
    assert artifact_path.is_relative_to(tmp_path)  # never the real lab output dir
    with Registry(registry_path, readonly=True) as registry:
        run = registry.run(experiment_id)
        family_rows = registry.candidates("nse:mean_reversion_v1:replacement")
    assert run is not None
    assert len(run["candidates"]) == 1
    assert run["candidates"][0]["id"] == candidate_id
    metrics = run["candidates"][0]["metrics"]
    # Recorded under the gauntlet's real key (`net_expectancy_r`); before 2026-09-29 it
    # read a key that doesn't exist, so net_r was always None and Registry.best() could
    # never find any candidate.
    assert metrics["net_r"] is not None
    assert "win_rate" in metrics and "fail_reasons" in metrics
    assert [r["id"] for r in family_rows] == [candidate_id]


def _report(stopped_at=None, kill_passed=True, p_value=0.0, net_r=0.2) -> GauntletReport:
    return GauntletReport(
        strategy_name="x",
        stopped_at=stopped_at,
        stages={
            "random_entry_benchmark": {"p_value": p_value},
            "in_sample": {"net_expectancy_r": net_r},
        },
        kill_criteria={"passed": kill_passed},
    )


def test_candidate_verdict_passes_only_when_every_bar_is_cleared() -> None:
    ok, reasons = da.candidate_verdict(
        _report(), True, baseline_net_r=-0.4, max_null_p_value=0.001
    )
    assert ok and reasons == []


@pytest.mark.parametrize(
    ("report", "beats", "kwargs", "needle"),
    [
        (_report(stopped_at="walk_forward"), True, {}, "stopped at walk_forward"),
        (_report(kill_passed=False), True, {}, "kill criteria"),
        (_report(), False, {}, "over random timing"),
        (_report(p_value=0.01), True, {"max_null_p_value": 0.001}, "search-adjusted"),
        (_report(net_r=-0.5), True, {"baseline_net_r": -0.4}, "does not beat the retired"),
    ],
)
def test_candidate_verdict_names_the_bar_that_was_missed(report, beats, kwargs, needle) -> None:
    ok, reasons = da.candidate_verdict(report, beats, **kwargs)
    assert not ok
    assert any(needle in r for r in reasons)


def test_generated_production_source_compiles_and_references_the_new_setup_kind() -> None:
    src = da._mean_reversion_v1_production_source("MEAN_REVERSION_V1")
    compile(src, "<generated>", "exec")
    assert "SetupKind.MEAN_REVERSION_V1" in src
    assert "class MeanReversionV1" in src


def test_propose_new_detector_returns_none_when_gauntlet_failed() -> None:
    failing_report = GauntletReport(
        strategy_name="x", stopped_at="random_entry_benchmark", stages={}, kill_criteria=None
    )
    result = da.propose_new_detector(MeanReversionV1(), "nse", failing_report, False, "e1", "c1")
    assert result is None


def test_propose_new_detector_names_the_exact_five_allowed_target_files() -> None:
    passing_report = GauntletReport(
        strategy_name="x", stopped_at=None, stages={}, kill_criteria={"passed": True}
    )
    result = da.propose_new_detector(
        MeanReversionV1(), "nse", passing_report, True, "e1", "c1"
    )
    assert result is not None
    assert result.target_files == [
        "src/tradedesk/engine/patterns.py",
        "src/tradedesk/setups/mean_reversion_v1.py",
        "src/tradedesk/engine/signals.py",
        "src/tradedesk/setups/__init__.py",
        "config/setups.yaml",
    ]
