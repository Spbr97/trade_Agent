"""Precision selection must preserve temporal causality and honest availability."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.dataset import Dataset
from tradedesk_lab.reliability import (
    _fold_predictions,
    choose_policy,
    run_reliability,
    select_calls,
    session_metrics,
)


def example_frame(dates=160, per_date=4):
    calendar = pd.bdate_range("2023-01-02", periods=dates + 8)
    armed = np.repeat(calendar[:dates], per_date)
    frame = pd.DataFrame(
        {
            "signal_id": [f"s{i:05}" for i in range(len(armed))],
            "scrip_code": [f"code{i % per_date}" for i in range(len(armed))],
            "armed_on": armed,
            "entry_date": armed + pd.Timedelta(days=1),
            "label_end_date": armed + pd.Timedelta(days=4),
            "regime": "risk_on",
            "label": 1,
            "net_r": 0.5,
            "score": 0.9,
        }
    )
    return frame, calendar


def test_selector_caps_at_prediction_time_dedupes_symbols_and_ignores_outcomes():
    frame, _ = example_frame(2)
    frame.loc[1, "scrip_code"] = frame.loc[0, "scrip_code"]
    p = np.array([0.91, 0.92, 0.83, 0.75, 0.61, 0.81, 0.72, 0.65])
    policy = {"threshold": 0.7, "top_k": 2, "regime": None}
    selected = select_calls(frame, p, policy)
    assert np.flatnonzero(selected).tolist() == [1, 2, 5, 6]
    frame["entry_date"] = pd.Timestamp("2099-01-01")
    frame["label"] = 0
    frame["net_r"] = -100
    assert np.array_equal(selected, select_calls(frame, p, policy))


def test_session_counts_include_days_with_no_calls_and_net_target_gate():
    frame, calendar = example_frame(25)
    frame.loc[:19, "label"] = 0
    metrics = session_metrics(frame, np.ones(len(frame), bool), calendar)
    assert metrics["precision"] == 0.8
    assert metrics["wilson_lower"] > 0.7
    assert metrics["zero_call_sessions"] == 8
    assert metrics["active_sessions"] == 25
    assert metrics["historical_target_met"] is False  # insufficient independent sessions
    assert "eventual outcomes" in metrics["session_definition"]


def test_policy_rejects_high_accuracy_with_negative_expectancy():
    frame, calendar = example_frame(120)
    frame["net_r"] = -0.1
    trials = []
    policy, evidence = choose_policy(frame, np.full(len(frame), 0.9), calendar, trials, "test")
    assert policy["threshold"] > 1
    assert evidence["qualifying_policies"] == 0
    assert len(trials) == 48
    assert not any(row["historical_target_met"] for row in trials)


@dataclass
class FakeBundle:
    features: list

    def predict(self, frame):
        return np.full(len(frame), 0.9)


def test_nested_predictions_never_train_on_evaluation_label_intervals(monkeypatch):
    frame, calendar = example_frame(240)
    train = frame.loc[frame.armed_on < calendar[210]].reset_index(drop=True)
    test = frame.loc[frame.armed_on >= calendar[220]].reset_index(drop=True)
    ds = Dataset(frame, ["score"], calendar, {}, {"label_version": "net-target-v2"})
    fits = []

    def mock_fit(family, hp, df, features, calendar):
        fits.append(df.copy())
        return FakeBundle(features)

    monkeypatch.setattr("tradedesk_lab.reliability.fit", mock_fit)
    trials = []
    inner, outer, bundles = _fold_predictions(train, test, ds, {"a": {}, "b": {}}, trials, "test")
    assert "stacked_logistic" in outer
    assert "stacked_logistic" in bundles
    assert np.isfinite(inner["stacked_logistic"]).sum() < np.isfinite(inner["a"]).sum()
    assert all(df.label_end_date.max() < test.armed_on.min() for df in fits)
    meta = fits[-1]
    meta_test = train.loc[np.isfinite(inner["stacked_logistic"])]
    assert meta.label_end_date.max() < meta_test.armed_on.min()
    assert all(name.startswith("probability_") for name in bundles["stacked_logistic"].features)


def test_research_export_preserves_active_cohort_and_declares_diagnostic_role(
    tmp_path, monkeypatch
):
    frame, calendar = example_frame()
    ds = Dataset(frame, ["score"], calendar, {}, {"label_version": "net-target-v2"})
    monkeypatch.setattr("tradedesk_lab.reliability.candidate_grid", lambda: ({"logistic": {}}, {}))
    monkeypatch.setattr(
        "tradedesk_lab.reliability.fit",
        lambda family, hp, df, features, calendar: FakeBundle(features),
    )
    (tmp_path / "latest.json").write_text('{"id":"old-cohort"}', encoding="utf-8")
    report = run_reliability(ds, output=tmp_path, outer_splits=2)
    assert (tmp_path / "latest.json").read_text() == '{"id":"old-cohort"}'
    assert (tmp_path / "reliability" / report["id"] / "contract.json").exists()
    assert report["eligibility"]["live_enabled"] is False
    assert report["eligibility"]["auto_promote"] is False
    assert "not fresh" in report["metadata"]["historical_role"]
    assert report["artifacts"]["logistic"]["sha256"]
    assert len(report["diagnostic_selector_grid"]) == 48


def test_legacy_target_labels_cannot_enter_new_research(tmp_path):
    frame, calendar = example_frame()
    ds = Dataset(frame, ["score"], calendar, {}, {})
    with pytest.raises(ValueError, match="net-target-v2"):
        run_reliability(ds, output=tmp_path)


def test_unpurged_outer_labels_cannot_reach_a_model(monkeypatch):
    frame, calendar = example_frame()
    train = frame.loc[frame.armed_on <= calendar[100]].reset_index(drop=True)
    test = frame.loc[frame.armed_on > calendar[100]].reset_index(drop=True)
    ds = Dataset(frame, ["score"], calendar, {}, {"label_version": "net-target-v2"})
    with pytest.raises(ValueError, match="overlap evaluation time"):
        _fold_predictions(train, test, ds, {"logistic": {}}, [], "test")


def test_post_outcome_feature_and_unresolved_rows_are_rejected(tmp_path):
    frame, calendar = example_frame()
    ds = Dataset(frame, ["score", "net_r"], calendar, {}, {"label_version": "net-target-v2"})
    with pytest.raises(ValueError, match="cannot be model features"):
        run_reliability(ds, output=tmp_path)
    ds.features = ["score"]
    ds.frame.loc[0, "net_r"] = np.nan
    with pytest.raises(ValueError, match="fully resolved"):
        run_reliability(ds, output=tmp_path)


def test_calibrator_only_observes_dates_after_training_label_resolution(monkeypatch):
    from tradedesk_lab.models import fit

    frame, calendar = example_frame(150)
    frame["label"] = np.arange(len(frame)) % 2
    fitted_rows = []

    class RecordingEstimator:
        def fit(self, features, labels):
            fitted_rows.append(features.index.to_numpy())
            return self

        def predict_proba(self, features):
            assert (
                frame.loc[fitted_rows[0], "label_end_date"].max()
                < frame.loc[features.index, "armed_on"].min()
            )
            return np.column_stack([np.full(len(features), 0.4), np.full(len(features), 0.6)])

    monkeypatch.setattr("tradedesk_lab.models.estimator", lambda *args: RecordingEstimator())
    fit("logistic", {"C": 0.1}, frame, ["score"], calendar)
    assert len(fitted_rows) == 1
