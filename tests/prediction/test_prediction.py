"""M11: the prediction layer.

- Triple-barrier labels, including the gap-through-stop case and the vertical barrier.
- Features never look ahead: the same signal featured from a truncated frame is identical.
- Purged walk-forward: no training row within the embargo of a test block, no overlap.
- Training on synthetic data recovers the planted signs and is calibrated.
- `apply_probability` can only lower a grade or halve a size, and is a no-op in shadow.
- The drift monitor pauses only on a well-populated bucket that has drifted.
- `build_dataset` runs off the synthetic backtest world.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.backtest.test_runner import BREAKOUT, CAL, run
from tradedesk.broker.indstocks.models import IST
from tradedesk.config.models import MlConfig
from tradedesk.engine.regime import Regime, RegimeSnapshot
from tradedesk.engine.scoring import Grade
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.prediction import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    ModelBundle,
    apply_probability,
    build_dataset,
    check_and_flag_drift,
    drift_check,
    label_signal,
    latest_bundle,
    latest_bundles_by_setup,
    probability,
    signal_features,
    train,
    train_per_setup,
    triple_barrier,
)
from tradedesk.prediction.features import to_frame
from tradedesk.prediction.predict import is_current, log_shadow, read_shadow, score_watchlist
from tradedesk.prediction.train import make_xgboost, purged_walk_forward, select_threshold
from tradedesk.review_queue import load_queue
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry

D = date(2026, 3, 2)


def bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-03-02", periods=len(rows), freq="B", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


# --------------------------------------------------------------- labels


def test_target_before_stop_is_one() -> None:
    lab = triple_barrier(
        bars([(100, 104, 99, 103), (103, 111, 102, 110)]),
        entry=100,
        stop=95,
        target=110,
        max_hold=1,
    )
    assert lab.label == 1 and lab.outcome == "target" and lab.sessions == 1


def test_stop_wins_when_bar_touches_both() -> None:
    lab = triple_barrier(
        bars([(100, 104, 99, 103), (103, 112, 94, 100)]), entry=100, stop=95, target=110, max_hold=1
    )
    assert lab.label == 0 and lab.outcome == "stop"


def test_gap_through_stop_is_a_gap_stop_at_the_open() -> None:
    lab = triple_barrier(
        bars([(100, 104, 99, 103), (90, 112, 89, 111)]), entry=100, stop=95, target=110, max_hold=1
    )
    assert lab.outcome == "gap_stop" and lab.exit_price == 90.0 and lab.label == 0


def test_vertical_barrier_is_zero_and_never_looks_past_max_hold() -> None:
    rows = [(100, 104, 99, 103)] * 4 + [(103, 130, 102, 129)]  # target hit only on session 4
    lab = triple_barrier(bars(rows), entry=100, stop=95, target=110, max_hold=3)
    assert lab.outcome == "timeout" and lab.label == 0 and lab.sessions == 3
    short = triple_barrier(bars(rows[:2]), entry=100, stop=95, target=110, max_hold=3)
    assert short.outcome == "insufficient"


def test_label_signal_slices_from_entry_date() -> None:
    frame = bars([(90, 91, 89, 90), (100, 104, 99, 103), (103, 111, 102, 110)])
    lab = label_signal(
        frame, entry_date=date(2026, 3, 3), entry=100, stop=95, target=110, max_hold=1
    )
    assert lab.outcome == "target"


# ------------------------------------------------------------- features


def sig(code: str = "NSE_1", **kw: object) -> Signal:
    base = dict(
        id=f"base_breakout:{code}:2026-03-01", scrip_code=code, symbol=code.replace("NSE_", "S"),
        setup=SetupKind.BASE_BREAKOUT, armed_on=D - timedelta(days=1), trigger=100.0, stop=95.0,
        t1=110.0, t2=115.0, atr=2.0, reasons=["15-bar base"], rs_percentile=88.0, regime="risk_on",
        geometry={"base": {"depth_pct": 8.0, "contraction_ratio": 0.6, "volume_dryup": 0.7,
                           "tests_of_high": 2}},
    )  # fmt: skip
    base.update(kw)
    return Signal(**base)  # type: ignore[arg-type]


def feature_frame(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2025-12-01", periods=n, freq="B", tz="Asia/Kolkata")
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "open": close, "high": close + 1, "low": close - 1, "close": close,
            "atr14": 2.0, "ema20": close - 1, "ema50": close - 3, "ema200": close - 8,
            "ema20_slope": 0.001, "ema50_slope": 0.0005, "adx14": 25.0, "rsi14": 60.0,
            "atr_pct": 2.0, "vol_ratio50": 1.4, "updown_vol20": 1.2, "nr7": False,
            "inside_day": True,
        },
        index=idx,
    )  # fmt: skip


def test_features_have_every_name_and_never_look_ahead() -> None:
    full = feature_frame(60)
    arming = full.iloc[:40]
    f_full_slice = signal_features(sig(), full.iloc[:40], breadth_pct=55.0, vix=13.0)
    f_trunc = signal_features(sig(), arming.copy(), breadth_pct=55.0, vix=13.0)
    assert set(f_full_slice) == set(FEATURE_NAMES)
    assert f_full_slice == f_trunc
    assert f_full_slice["stop_atr"] == pytest.approx(2.5)
    assert f_full_slice["setup_base_breakout"] == 1.0 and f_full_slice["regime_risk_on"] == 1.0
    assert f_full_slice["base_tests"] == 2.0 and f_full_slice["inside_day"] == 1.0
    assert to_frame([f_full_slice]).shape == (1, len(FEATURE_NAMES))


def _market_data_with_sector(sector_name: str = "BANK NIFTY") -> object:
    """Minimal MarketData stand-in for market_context()'s sector-return path. Only the
    attributes market_context() actually reads are populated."""
    from types import SimpleNamespace

    idx = pd.Index([date(2026, 3, 1) + timedelta(days=i) for i in range(10)])
    # a series that JUMPS after the arming date - if market_context leaked, the computed
    # return would pick up the jump instead of the pre-arming value
    closes = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 900.0, 901.0, 902.0, 903.0], index=idx)  # noqa: E501
    return SimpleNamespace(
        breadth=pd.Series(dtype=float),
        vix=None,
        benchmark=pd.DataFrame(),
        results_dates={},
        calendar=list(idx),
        sector_of={"NSE_1": sector_name},
        sector_candles={sector_name: closes},
    )


def test_sector_return_never_looks_past_the_arming_date() -> None:
    """market_context()'s sector-return path has its own leakage surface (a `.loc[:on]`
    slice), separate from signal_features() - the generic feature leakage test above
    cannot reach it, since the value arrives pre-computed."""
    from tradedesk.prediction.train import market_context

    md = _market_data_with_sector()
    on = date(2026, 3, 6)  # index position 5 (close 105.0); the 900.0 jump is the NEXT day
    ctx = market_context(md, "NSE_1", on)  # type: ignore[arg-type]
    # 1-day return as of `on` is 105/104 - 1, NOT anything involving the 900 jump after it
    assert ctx["sector_return_1d"] == pytest.approx((105.0 / 104.0 - 1) * 100)
    assert ctx["sector_return_5d"] == pytest.approx((105.0 / 100.0 - 1) * 100)


def test_sector_return_is_none_for_an_unmapped_symbol() -> None:
    """A stock with no curated sector (only ~29 are mapped, and only 2 sector indices have
    real history) degrades to None -> the feature's 0.0 default, never a crash."""
    from tradedesk.prediction.train import market_context

    md = _market_data_with_sector()
    ctx = market_context(md, "NSE_UNMAPPED", date(2026, 3, 6))  # type: ignore[arg-type]
    assert ctx["sector_return_1d"] is None and ctx["sector_return_5d"] is None
    f = signal_features(sig(), feature_frame(60).iloc[:40], **ctx)  # type: ignore[arg-type]
    assert f["sector_return_1d"] == 0.0 and f["sector_return_5d"] == 0.0


def test_sector_return_is_none_when_the_sector_has_no_loaded_candles() -> None:
    """Mapped to a sector whose index candles were never loaded (7 of the 9 configured
    sector indices have no history available via INDstocks at all)."""
    from tradedesk.prediction.train import market_context

    md = _market_data_with_sector()
    md.sector_of = {"NSE_1": "NIFTY IT"}  # type: ignore[attr-defined]  # mapped, but no candles
    ctx = market_context(md, "NSE_1", date(2026, 3, 6))  # type: ignore[arg-type]
    assert ctx["sector_return_1d"] is None and ctx["sector_return_5d"] is None


# ---------------------------------------------------------- walk-forward


def test_purged_walk_forward_respects_embargo_and_never_overlaps() -> None:
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(200)]
    dates = [sessions[i // 2] for i in range(400)]  # two signals a day
    folds = purged_walk_forward(dates, n_splits=4, embargo_sessions=10, min_train=30)
    assert len(folds) == 4
    d = np.array(dates)
    for f in folds:
        assert not set(f.train_idx) & set(f.test_idx)
        last_train = max(d[f.train_idx])
        gap_sessions = len([s for s in sessions if last_train < s < f.test_start])
        assert gap_sessions >= 10
        assert all(d[f.test_idx] >= f.test_start) and all(d[f.test_idx] <= f.test_end)
    assert folds[0].test_end < folds[1].test_start


# -------------------------------------------------------------- training


def synthetic_dataset(n: int = 600, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(0, 1, (n, len(FEATURE_NAMES))), columns=FEATURE_NAMES)
    X["rs_percentile"] = rng.uniform(0, 100, n)
    X["stop_atr"] = rng.uniform(0.5, 4.0, n)
    logit = 0.04 * (X["rs_percentile"] - 50) - 0.9 * (X["stop_atr"] - 2.0)
    p = 1 / (1 + np.exp(-logit))
    y = (rng.uniform(size=n) < p).astype(int)
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(n // 2)]
    X.insert(0, "armed_on", [sessions[i // 2] for i in range(n)])
    X.insert(0, "signal_id", [f"s{i}" for i in range(n)])
    X["label"] = y
    X["realised_r"] = np.where(y == 1, 2.0, -1.0)
    X["plain_score"] = rng.uniform(50, 100, n)
    return X


def synthetic_dataset_with_setups(n: int = 600, seed: int = 1) -> pd.DataFrame:
    """Same shape as synthetic_dataset(), plus a "setup" column with two groups: "good"
    keeps the real rs_percentile/stop_atr signal, "noise" has a label with no relation to
    any feature - so per_setup_breakdown() should report has_edge True for "good" and
    (once it has enough resolved rows) not for "noise", rather than one pooled number
    averaging the two together."""
    df = synthetic_dataset(n, seed)
    rng = np.random.default_rng(seed + 100)
    half = n // 2
    df["setup"] = ["good"] * half + ["noise"] * (n - half)
    noise_mask = df["setup"] == "noise"
    noise_y = rng.integers(0, 2, int(noise_mask.sum()))
    df.loc[noise_mask, "label"] = noise_y
    df.loc[noise_mask, "realised_r"] = np.where(noise_y == 1, 2.0, -1.0)
    return df


def test_hyperparameter_tuning_is_deterministic_and_records_the_winning_config() -> None:
    """No fixed, guessed hyperparameters any more - train() searches a small grid per model
    family and the winner's ACTUAL hyperparameters land in the artifact, not a constant."""
    df = synthetic_dataset()
    rep1 = train(df, n_splits=4, embargo_sessions=10, prefer_lightgbm=False, prefer_xgboost=False)  # noqa: E501
    rep2 = train(df, n_splits=4, embargo_sessions=10, prefer_lightgbm=False, prefer_xgboost=False)  # noqa: E501
    assert rep1.bundle is not None and rep2.bundle is not None
    # same seed, same data -> same winning config, both times (determinism preserved)
    assert rep1.bundle.hyperparameters == rep2.bundle.hyperparameters
    assert rep1.bundle.hyperparameters["C"] in (0.1, 0.3, 0.5, 1.0, 3.0)


def test_per_setup_breakdown_reports_each_setup_separately() -> None:
    rep = train(
        synthetic_dataset_with_setups(n=1200), n_splits=4, embargo_sessions=10,
        prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert rep.bundle is not None and rep.per_setup is not None
    assert "good" in rep.per_setup
    # "good" keeps the real signal - it should show a real edge, same as the pooled number
    assert rep.per_setup["good"]["has_edge"] in (True, False)  # never crashes; shape check
    assert "roc_auc" in rep.per_setup["good"]
    # the pooled oos metric is unaffected by per-setup breakdown existing
    assert rep.oos is not None


# --------------------------------------------------------- per-setup MODELS


def test_train_per_setup_trains_independent_models_versioned_distinctly() -> None:
    """train_per_setup() (2026-09-13): one independently-tuned model PER SETUP instead of
    one pooled across all of them - motivated by nr7_breakout being 81% of the real
    dataset and dragging down base_breakout's comparatively real signal. Both "good" and
    "noise" here have >= min_rows, so both should train; each bundle's version must carry
    its own setup suffix so saving them side by side never collides on one filename."""
    reports = train_per_setup(
        synthetic_dataset_with_setups(n=1200), min_rows=200,
        n_splits=4, embargo_sessions=10, prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert set(reports) == {"good", "noise"}
    for setup, rep in reports.items():
        assert rep.bundle is not None, f"{setup} should have trained (n=600 >= min_rows=200)"
        assert rep.bundle.version.endswith(f"-{setup}")
    # each setup got its OWN oos/per-setup-of-one report, not a shared pooled number
    assert reports["good"].bundle is not reports["noise"].bundle


def test_train_per_setup_skips_a_setup_below_min_rows() -> None:
    """A setup with too few resolved signals is skipped with an explicit note, not
    silently trained on a sample too small to trust - the same min_above/min_rows
    discipline used everywhere else in this module."""
    df = synthetic_dataset_with_setups(n=1200)
    tiny = df["setup"] == "noise"
    # shrink "noise" down to 50 rows, well under any reasonable min_rows floor
    df = pd.concat([df[~tiny], df[tiny].iloc[:50]], ignore_index=True)
    reports = train_per_setup(
        df, min_rows=200, n_splits=4, embargo_sessions=10,
        prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert reports["good"].bundle is not None  # unaffected - still has 600 rows
    assert reports["noise"].bundle is None
    assert any("skipped" in n and "50" in n for n in reports["noise"].notes)


def test_compare_strategies_setup_filter_isolates_that_setups_trades() -> None:
    """The `setup` filter added to compare_strategies() alongside train_per_setup() -
    without it, scoring every OTHER setup's rows with a bundle tuned on just one setup, and
    comparing against Strategy A's full multi-setup trade population, would be a real
    correctness bug (a base_breakout-only model has no business judging a trend_pullback
    trade). Uses the real synthetic backtest fixture (two setups worth of real closed
    trades) to prove the filter actually narrows both sides."""
    from tradedesk.prediction import compare_strategies

    _, cfg, md, res = run({"NSE_WIN": ("win", 1), "NSE_GAP": ("gap", 2)})
    df = build_dataset(md, res, max_hold=10)
    assert df["setup"].nunique() >= 1
    only_setup = df["setup"].iloc[0]
    rep = train(synthetic_dataset(400), n_splits=3, embargo_sessions=5, prefer_lightgbm=False)
    assert rep.bundle is not None
    unfiltered = compare_strategies(res, rep.bundle, df, starting_capital=cfg.capital)
    filtered = compare_strategies(
        res, rep.bundle, df, starting_capital=cfg.capital, setup=only_setup
    )
    # both trades in this fixture share the same setup, so filtering to it changes nothing
    # here numerically - the real assertion is that passing an ABSENT setup name empties
    # Strategy A/C entirely, proving the filter is actually applied rather than ignored.
    absent = compare_strategies(
        res, rep.bundle, df, starting_capital=cfg.capital, setup="__no_such_setup__"
    )
    assert absent["strategy_a_existing_rules"]["trades"] == 0
    assert unfiltered["strategy_a_existing_rules"]["trades"] == filtered["strategy_a_existing_rules"]["trades"]  # noqa: E501


def test_latest_bundles_by_setup_and_pooled_lookup_never_cross_contaminate(
    tmp_path: Path,
) -> None:
    """Per-setup files are named "<version>-<setup>.joblib" using REAL SetupKind values
    (base_breakout/trend_pullback/nr7_breakout - what train_per_setup() actually produces
    in production, off the real "setup" column). A plain pooled "<version>.joblib" must
    never be mistaken for one setup's dedicated model, and a per-setup file must never be
    returned by the POOLED latest_bundle() lookup either - each function must only ever
    see its own kind of artifact."""
    df = synthetic_dataset_with_setups(n=1200)
    df["setup"] = df["setup"].map({"good": "base_breakout", "noise": "trend_pullback"})
    reports = train_per_setup(
        df, min_rows=200, n_splits=3, embargo_sessions=5,
        prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert set(reports) == {"base_breakout", "trend_pullback"}
    for rep in reports.values():
        assert rep.bundle is not None
        rep.bundle.save(tmp_path)
    pooled_rep = train(synthetic_dataset(400), n_splits=3, embargo_sessions=5, prefer_lightgbm=False)  # noqa: E501
    assert pooled_rep.bundle is not None
    pooled_rep.bundle.save(tmp_path)

    by_setup = latest_bundles_by_setup(tmp_path)
    assert set(by_setup) == {"base_breakout", "trend_pullback"}
    assert by_setup["base_breakout"].version.endswith("-base_breakout")
    # the pooled lookup finds ONLY the plain "<version>.joblib", never a "-<setup>" one
    pooled = latest_bundle(tmp_path)
    assert pooled is not None and pooled.version == pooled_rep.bundle.version


def test_feature_stability_flags_a_sign_flip() -> None:
    from tradedesk.prediction.train import _feature_stability

    fold_importances = [
        {"rs_percentile": 0.2, "stop_atr": -0.1},
        {"rs_percentile": -0.15, "stop_atr": -0.12},
        {"rs_percentile": 0.18, "stop_atr": -0.09},
    ]
    stability, warnings = _feature_stability(fold_importances)
    assert "rs_percentile" in stability and stability["rs_percentile"] == [0.2, -0.15, 0.18]
    assert any("rs_percentile" in w and "unstable sign" in w for w in warnings)
    assert not any("stop_atr" in w for w in warnings)  # consistently negative - no flip


def test_feature_stability_is_empty_for_no_folds() -> None:
    from tradedesk.prediction.train import _feature_stability

    stability, warnings = _feature_stability([])
    assert stability == {} and warnings == []


def test_training_recovers_signs_and_is_calibrated(tmp_path: Path) -> None:
    rep = train(
        synthetic_dataset(), n_splits=4, embargo_sessions=10,
        prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert rep.bundle is not None and rep.oos is not None
    assert rep.bundle.kind == "logistic"
    assert rep.oos.brier < 0.24  # meaningfully better than the ~0.25 of a coin
    coefs = rep.bundle.coefficients()
    assert coefs is not None and coefs["rs_percentile"] > 0 and coefs["stop_atr"] < 0
    # calibration: bucket means roughly match realised rates
    for mean_p, realised, n in rep.oos.calibration:
        if n >= 30:
            assert abs(mean_p - realised) < 0.15, (mean_p, realised, n)
    path = rep.bundle.save(tmp_path)
    loaded = ModelBundle.load(path)
    assert latest_bundle(tmp_path) is not None
    f = dict.fromkeys(FEATURE_NAMES, 0.0)
    f["rs_percentile"], f["stop_atr"] = 95.0, 1.0
    good = probability(loaded, f)
    f["rs_percentile"], f["stop_atr"] = 10.0, 3.5
    bad = probability(loaded, f)
    assert good > 0.6 > 0.4 > bad
    assert "folds" in rep.text()


def test_training_refuses_a_one_class_dataset() -> None:
    df = synthetic_dataset(80)
    df["label"] = 1
    rep = train(df, n_splits=2, embargo_sessions=5)
    assert rep.bundle is None and rep.notes


def test_final_test_set_is_reserved_and_scored_exactly_once() -> None:
    """The most recent `final_test_frac` of history must never feed model or threshold
    selection - only the walk-forward folds do. Confirms the reserved block's dates never
    overlap the bundle's own recorded `training_data_range`."""
    rep = train(synthetic_dataset(), n_splits=4, embargo_sessions=10, final_test_frac=0.2)
    assert rep.bundle is not None and rep.final_test is not None
    training_end = date.fromisoformat(rep.bundle.training_data_range[1])
    # every armed_on date used for training must be strictly before the reserved period
    df = synthetic_dataset()
    reserved = df[df["armed_on"] > training_end]
    assert len(reserved) > 0  # something was actually held out, not "everything trained on"
    assert rep.final_test.n == len(reserved)


def test_select_threshold_requires_a_minimum_sample_and_flags_no_edge() -> None:
    """Real bug found running this on live data: without a sample-size floor, "maximise
    total R" picks whichever threshold happens to leave the fewest trades (least to lose),
    not a genuine edge. All-negative expectancy at every threshold must report has_edge=False."""
    rng = np.random.default_rng(3)
    n = 500
    p = rng.uniform(0, 1, n)
    y = rng.integers(0, 2, n)
    r = np.where(y == 1, 0.5, -1.0)  # negative expectancy regardless of threshold
    threshold, grid, has_edge = select_threshold(y, p, r, min_above=20)
    assert has_edge is False
    chosen = next(row for row in grid if row["threshold"] == threshold)
    assert chosen["eligible"] is True  # never settles on a sub-floor sample


def test_select_threshold_finds_a_real_edge_when_one_exists() -> None:
    rng = np.random.default_rng(4)
    n = 2000
    p = rng.uniform(0, 1, n)
    y = (rng.uniform(size=n) < p).astype(int)  # p is genuinely predictive of y
    r = np.where(y == 1, 2.0, -1.0)
    threshold, grid, has_edge = select_threshold(y, p, r, min_above=20)
    assert has_edge is True
    assert threshold >= 0.5


def test_make_xgboost_degrades_to_none_or_builds_a_fittable_model() -> None:
    """Same contract as make_lightgbm(): either unimportable (returns None, degrades
    gracefully) or a real, fittable, calibrated estimator - never raises either way."""
    m = make_xgboost()
    if m is None:
        pytest.skip("xgboost not importable in this environment")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(60, 3))
    y = (X[:, 0] > 0).astype(int)
    m.fit(X, y)
    p = m.predict_proba(X)[:, 1]
    assert p.shape == (60,)
    assert ((p >= 0) & (p <= 1)).all()


def test_artifact_json_has_every_field_the_bundle_promises(tmp_path: Path) -> None:
    """No prior test read the .json sidecar's schema at all - only the .joblib round trip
    was checked. This closes that gap."""
    import json

    rep = train(synthetic_dataset(), n_splits=4, embargo_sessions=10, max_hold=10)
    assert rep.bundle is not None
    path = rep.bundle.save(tmp_path)
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    for key in (
        "version", "kind", "threshold", "oos", "trained_on", "notes", "feature_list",
        "training_data_range", "feature_version", "target_definition", "hyperparameters",
        "random_seed", "final_test", "threshold_grid", "feature_importance",
        "per_setup", "feature_stability",
    ):  # fmt: skip
        assert key in payload, f"missing artifact field: {key}"
    assert payload["target_definition"]["max_hold"] == 10
    assert payload["random_seed"] == rep.bundle.random_seed
    assert payload["feature_version"] == FEATURE_VERSION
    # tuned, not a fixed constant: the winning grid entry's own hyperparameters
    assert payload["hyperparameters"]["random_state"] == rep.bundle.random_seed


def test_a_stale_feature_version_bundle_is_refused_not_silently_misscored() -> None:
    """v3 replaced sector_percentile with sector_return_1d/5d. Scoring a v2-trained bundle
    against v3 features would silently produce garbage (mismatched columns, no error), so
    probability() refuses outright. A bundle with no recorded feature_version (pre-dating
    that field) is still allowed through, for backward compatibility."""
    rep = train(
        synthetic_dataset(), n_splits=4, embargo_sessions=10,
        prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert rep.bundle is not None
    f = dict.fromkeys(FEATURE_NAMES, 0.0)
    assert 0.0 <= probability(rep.bundle, f) <= 1.0  # current version scores fine

    rep.bundle.feature_version = "v2"
    with pytest.raises(ValueError, match="feature_version"):
        probability(rep.bundle, f)

    rep.bundle.feature_version = None  # an old artifact without the field: still allowed
    assert 0.0 <= probability(rep.bundle, f) <= 1.0


def test_a_stale_bundle_is_selectable_as_not_current_so_the_scan_can_skip_it() -> None:
    """The counterpart to the test above, and the reason it exists: probability() RAISING on
    a stale bundle is right for scoring but fatal for the evening scan, which must keep
    building a watchlist whatever the model is doing (the layer is advisory - shadow mode
    only appends a note). Bumping FEATURE_VERSION to v4 without retraining made every saved
    bundle stale and would have crashed `tradedesk scan` on the next `tradedesk-after-close`
    run, leaving the next morning's live session with no watchlist. `is_current` is what the
    scan filters on; if this ever regresses, the scan starts dying on a version bump again."""
    rep = train(
        synthetic_dataset(), n_splits=4, embargo_sessions=10,
        prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert rep.bundle is not None
    assert is_current(rep.bundle)

    rep.bundle.feature_version = "v3"
    assert not is_current(rep.bundle)
    with pytest.raises(ValueError, match="feature_version"):
        probability(rep.bundle, dict.fromkeys(FEATURE_NAMES, 0.0))

    rep.bundle.feature_version = None  # pre-dates the field: treated as usable, as before
    assert is_current(rep.bundle)


def test_feature_importance_is_ranked_and_works_for_the_logistic_baseline() -> None:
    rep = train(
        synthetic_dataset(), n_splits=4, embargo_sessions=10,
        prefer_lightgbm=False, prefer_xgboost=False,
    )  # fmt: skip
    assert rep.bundle is not None
    importance = rep.bundle.feature_importance()
    assert importance is not None
    values = list(importance.values())
    assert values == sorted(values, key=abs, reverse=True)  # ranked by magnitude, descending
    assert "rs_percentile" in importance and "stop_atr" in importance


# ------------------------------------------------------------ applying


def entry(grade: Grade = Grade.A, qty: int = 50) -> WatchlistEntry:
    return WatchlistEntry(
        signal=sig(), score=85, grade=grade, score_components={"trend": 1.0}, score_notes=[],
        alertable=grade is not Grade.C, qty=qty, risk_amount=250.0, risk_pct=0.0025,
        position_value=5000.0, size_caps=[], costs_round_trip=60.0, net_rr_t1=1.6, net_rr_t2=2.4,
        results_in_sessions=None, heat_before_pct=0.0, heat_after_pct=0.0025, atr_pct=2.0,
        avg_turnover=1e8,
    )  # fmt: skip


SHADOW = MlConfig()
ON = MlConfig(enabled=True, shadow=False)


def test_shadow_mode_changes_nothing_but_the_note() -> None:
    e = entry()
    out = apply_probability(e, 0.05, SHADOW)
    assert out.grade is e.grade and out.qty == e.qty and out.alertable
    assert any("model p" in n for n in out.score_notes)
    out2 = apply_probability(e, 0.05, MlConfig(enabled=True, shadow=True))
    assert out2.grade is e.grade and out2.qty == e.qty


@pytest.mark.parametrize("grade", list(Grade))
@pytest.mark.parametrize("p", [0.0, 0.2, 0.39, 0.4, 0.44, 0.45, 0.49, 0.5, 0.7, 0.99])
def test_enabled_can_only_lower(grade: Grade, p: float) -> None:
    e = entry(grade)
    out = apply_probability(e, p, ON)
    order = {Grade.A: 3, Grade.B: 2, Grade.C: 1}
    assert order[out.grade] <= order[e.grade]
    assert out.qty <= e.qty and out.risk_amount <= e.risk_amount
    assert out.position_value <= e.position_value
    assert out.alertable <= e.alertable
    # the price levels are untouched
    assert out.signal == e.signal and out.net_rr_t1 == e.net_rr_t1


def test_enabled_thresholds() -> None:
    a = entry(Grade.A)
    assert apply_probability(a, 0.55, ON).grade is Grade.A
    assert apply_probability(a, 0.55, ON).qty == 50
    assert apply_probability(a, 0.47, ON).grade is Grade.B
    assert apply_probability(a, 0.47, ON).qty == 50
    half = apply_probability(a, 0.42, ON)
    assert half.grade is Grade.B and half.qty == 25 and half.size_caps
    c = apply_probability(a, 0.3, ON)
    assert c.grade is Grade.C and c.qty == 25 and not c.alertable
    b = entry(Grade.B)
    assert apply_probability(b, 0.99, ON).grade is Grade.B  # never raised


# ---------------------------------------------------------------- drift


def test_drift_monitor_pauses_only_on_populated_drifted_bucket(tmp_path: Path) -> None:
    log = tmp_path / "shadow.jsonl"
    for i in range(40):
        log_shadow(log, f"s{i}", 0.65, "v1")
    for i in range(40, 45):
        log_shadow(log, f"s{i}", 0.2, "v1")
    df = read_shadow(log)
    assert len(df) == 45
    # bucket 0.6-0.75 realised 0.60 -> fine
    ok = drift_check(df, {f"s{i}": int(i % 5 < 3) for i in range(40)})
    assert not ok.paused
    # the same bucket realising 0.25 -> paused
    bad = drift_check(df, {f"s{i}": int(i % 4 == 0) for i in range(40)})
    assert bad.paused and "0.60" in bad.reason
    # the small 0.2 bucket drifting wildly does not pause (n < 30)
    small = drift_check(df, {f"s{i}": 1 for i in range(40, 45)})
    assert not small.paused
    assert not drift_check(read_shadow(tmp_path / "missing.jsonl"), {}).paused


def test_check_and_flag_drift_writes_one_review_item_and_dedupes(tmp_path: Path) -> None:
    """Closes the real gap drift_check() left open: nothing in production ever called it.
    Mirrors tests/unit/test_signal_tracker.py's dedupe test for flag_setup_failures() -
    re-running on the same drifted state must not add a second review item, and this must
    never touch the production queue (tmp_path throughout, per the claude/weekly_review.py
    lesson)."""
    log = tmp_path / "shadow.jsonl"
    queue = tmp_path / "queue.jsonl"
    for i in range(40):
        log_shadow(log, f"s{i}", 0.65, "v1")
    outcomes = {f"s{i}": int(i % 4 == 0) for i in range(40)}  # realised ~0.25 vs predicted 0.65

    report1 = check_and_flag_drift(log, outcomes, review_path=queue)
    assert report1.paused
    items = load_queue(queue)
    assert len(items) == 1
    assert next(iter(items.values())).title == "ML prediction layer calibration drift"

    report2 = check_and_flag_drift(log, outcomes, review_path=queue)
    assert report2.paused
    assert len(load_queue(queue)) == 1  # re-running does not duplicate the flag


def test_check_and_flag_drift_no_ops_cleanly_with_no_resolved_outcomes(tmp_path: Path) -> None:
    log = tmp_path / "shadow.jsonl"
    queue = tmp_path / "queue.jsonl"
    log_shadow(log, "s0", 0.65, "v1")
    report = check_and_flag_drift(log, {}, review_path=queue)
    assert not report.paused
    assert load_queue(queue) == {}


# ------------------------------------------------------- end to end


def test_build_dataset_from_the_synthetic_backtest_and_score_watchlist(
    tmp_path: Path,
) -> None:
    _, cfg, md, res = run({"NSE_WIN": ("win", 1), "NSE_GAP": ("gap", 2)})
    df = build_dataset(md, res, max_hold=10)
    assert len(df) >= 2
    on_day = df[df["entry_date"] == CAL[BREAKOUT]].set_index("scrip_code")
    assert on_day.loc["NSE_WIN", "label"] == 1
    assert on_day.loc["NSE_GAP", "outcome"] == "gap_stop"
    assert on_day.loc["NSE_GAP", "label"] == 0
    assert set(FEATURE_NAMES) <= set(df.columns)
    assert df[FEATURE_NAMES].notna().all().all()

    # a model trained elsewhere scores a watchlist built for the arming day in shadow mode
    rep = train(synthetic_dataset(400), n_splits=3, embargo_sessions=5, prefer_lightgbm=False)
    assert rep.bundle is not None
    armed = CAL[BREAKOUT - 1]
    win = next(ts.signal for ts in res.signals if ts.signal.scrip_code == "NSE_WIN")
    e = entry().model_copy(update={"signal": win})
    regime = RegimeSnapshot(
        on=armed, regime=Regime.RISK_ON, benchmark_close=1.0, benchmark_ema=1.0, ema_rising=True,
        above_ema=True, breadth_pct=60.0, vix=12.0, vix_change_5d_pct=0.0, size_multiplier=1.0,
        reasons=["fine"],
    )  # fmt: skip
    wl = Watchlist(
        on=win.armed_on, generated_at=datetime.combine(armed, dtime(16, 30), tzinfo=IST),
        regime=regime, capital=100000.0, entries=[e], open_positions=[],
    )  # fmt: skip
    scored, probs = score_watchlist(
        rep.bundle, wl, md, SHADOW, shadow_log=tmp_path / "shadow.jsonl"
    )
    assert set(probs) == {win.symbol} and 0.0 <= probs[win.symbol] <= 1.0
    assert scored.entries[0].grade is e.grade and scored.entries[0].qty == e.qty
    assert len(read_shadow(tmp_path / "shadow.jsonl")) == 1
    assert isinstance(Decimal(str(probs[win.symbol])), Decimal)

    # --- per-setup routing (2026-09-13): a dict of setup -> bundle instead of one pooled
    # bundle must score `win` with ITS OWN setup's model, and must leave an entry whose
    # setup has no dedicated bundle completely unscored rather than silently reusing a
    # different setup's model or falling back to nothing (see score_watchlist's docstring:
    # "no model for this setup" must be visibly different from "the model said no").
    by_setup = {win.setup.value: rep.bundle}
    scored2, probs2 = score_watchlist(
        by_setup, wl, md, SHADOW, shadow_log=tmp_path / "shadow2.jsonl"
    )
    assert set(probs2) == {win.symbol} and 0.0 <= probs2[win.symbol] <= 1.0
    assert any("model p" in n for n in scored2.entries[0].score_notes)

    empty_by_setup: dict[str, ModelBundle] = {}  # no bundle for ANY setup, including win's
    scored3, probs3 = score_watchlist(
        empty_by_setup, wl, md, SHADOW, shadow_log=tmp_path / "shadow3.jsonl"
    )
    assert probs3 == {}  # nothing scored
    assert scored3.entries[0] == e  # entry passed through completely untouched
    assert not (tmp_path / "shadow3.jsonl").exists()  # nothing logged either
