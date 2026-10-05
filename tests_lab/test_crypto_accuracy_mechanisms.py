from __future__ import annotations

import json
import sys
from dataclasses import FrozenInstanceError, asdict, fields, replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tradedesk_lab.crypto_accuracy_mechanisms as mechanisms_module

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tradedesk_lab.crypto_accuracy_mechanisms import (  # noqa: E402
    CONTROL_IDS,
    DEFAULT_CONTRACT,
    GEOMETRY_IDS,
    MECHANISM_IDS,
    VERSION,
    CryptoMechanismContract,
    _control_indices,
    _latch_terminal,
    _verified_terminal,
    build_feature_panel,
    evaluate_mechanism_race,
    join_frozen_labels,
    mechanism_metrics,
    run_crypto_accuracy_mechanisms,
)

FORBIDDEN_OUTCOME_COLUMNS = {
    "status",
    "label",
    "outcome",
    "entry_at",
    "entry_session",
    "entry_price",
    "stop",
    "target",
    "exit_at",
    "exit_price",
    "gross_r",
    "net_r",
    "after_tax_r",
    "eligible_for_live",
    "mfe",
    "mae",
}


def _daily_history(*, pair_count: int = 210, session_count: int = 30) -> pd.DataFrame:
    sessions = pd.date_range("2026-01-01", periods=session_count, freq="D", tz="UTC")
    scrip_codes = [
        "CDX_BTCINR",
        *(f"COIN{index:03d}USDT" for index in range(pair_count - 1)),
    ]
    rows: list[dict[str, object]] = []

    for pair_index, scrip_code in enumerate(scrip_codes):
        base = 80.0 + pair_index * 0.4
        for day_index, session in enumerate(sessions):
            # Deliberately uses only the pair and current/past session coordinates.
            # Cross-sectional dispersion keeps ranks meaningful and a late pullback
            # leaves both continuation and pullback populations in the panel.
            trend = 1.0 + day_index * (0.004 + pair_index * 0.000002)
            cycle = 0.006 * np.sin(day_index / 2.7 + pair_index / 11.0)
            late_pullback = max(day_index - 26, 0) * (0.002 + (pair_index % 7) * 0.00015)
            close = base * (trend + cycle - late_pullback)
            rows.append(
                {
                    "scrip_code": scrip_code,
                    "session": session,
                    "closed_at": session + pd.Timedelta(hours=23, minutes=59),
                    "open": close * (0.997 + (pair_index % 3) * 0.0005),
                    "high": close * (1.011 + (pair_index % 5) * 0.0002),
                    "low": close * (0.989 - (pair_index % 4) * 0.0002),
                    "close": close,
                    "volume": 10_000.0 + pair_index * 31.0 + day_index * 7.0,
                    "quality_status": "valid",
                    "membership_status": "active",
                    "configured_excluded": False,
                    "point_in_time_eligible": True,
                }
            )

    return pd.DataFrame(rows)


def _sort_features(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["decision_session", "scrip_code", "mechanism"]
    return frame.sort_values(keys, kind="stable").reset_index(drop=True)


def _join_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    decision_session = pd.Timestamp("2026-01-30", tz="UTC")
    features = pd.DataFrame(
        [
            {
                "scrip_code": scrip_code,
                "decision_session": decision_session,
                "decision_at": decision_session + pd.Timedelta(hours=23, minutes=59),
                "mechanism": mechanism,
                "score": float(pair_index + mechanism_index / 10),
                "score_rank_pct": 0.25 + pair_index * 0.5,
                "registered_active_count": 210,
                "feature_valid_count": 210,
                "cross_section_coverage": 1.0,
                "pool_size": 2,
                "feature_status": "eligible",
                "exclusion_reason": None,
                "pool_eligible": True,
                "selected": pair_index == 0,
                "inverse_selected": pair_index == 1,
            }
            for pair_index, scrip_code in enumerate(("COIN000USDT", "COIN001USDT"))
            for mechanism_index, mechanism in enumerate(MECHANISM_IDS)
        ]
    )
    labels = pd.DataFrame(
        [
            {
                "scrip_code": scrip_code,
                "decision_session": decision_session,
                "geometry": geometry,
                "point_in_time_eligible": True,
                "status": "resolved",
                "label": pair_index % 2,
                "gross_r": 0.5 - pair_index,
                "net_r": 0.4 - pair_index,
                "after_tax_r": 0.3 - pair_index,
            }
            for pair_index, scrip_code in enumerate(("COIN000USDT", "COIN001USDT"))
            for geometry in GEOMETRY_IDS
        ]
    )
    return features, labels


def _control_feature_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for decision_session in pd.date_range("2026-02-01", periods=8, freq="D", tz="UTC"):
        for pair_index in range(10):
            rows.append(
                {
                    "scrip_code": f"COIN{pair_index:03d}USDT",
                    "decision_session": decision_session,
                    "decision_at": decision_session + pd.Timedelta(hours=23, minutes=59),
                    "mechanism": MECHANISM_IDS[0],
                    "score": float(pair_index),
                    "score_rank_pct": (pair_index + 1) / 10,
                    "registered_active_count": 210,
                    "feature_valid_count": 210,
                    "cross_section_coverage": 1.0,
                    "pool_size": 10,
                    "feature_status": "eligible",
                    "exclusion_reason": None,
                    "pool_eligible": True,
                    "selected": pair_index >= 8,
                    "inverse_selected": pair_index < 2,
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["decision_session", "scrip_code"], kind="stable"
    ).reset_index(drop=True)


def test_frozen_contract_is_exact_and_rejects_every_override() -> None:
    expected = {
        "version": "crypto-accuracy-mechanisms-v1",
        "mechanism_ids": MECHANISM_IDS,
        "geometry_ids": GEOMETRY_IDS,
        "control_ids": CONTROL_IDS,
        "development_sessions": 30,
        "fold_count": 3,
        "selection_fraction": 0.20,
        "liquid_universe_fraction": 0.50,
        "minimum_cross_section": 200,
        "minimum_cross_section_coverage": 0.90,
        "cohort_repetitions": 1000,
        "cohort_seed": 20261007,
        "minimum_pair_sessions": 30,
        "minimum_resolved_calls": 100,
        "minimum_active_sessions": 30,
        "minimum_accuracy": 0.80,
        "minimum_wilson95_lower": 0.70,
        "minimum_mean_net_r": 0.0,
        "minimum_control_advantage_r": 0.10,
        "maximum_familywise_empirical_p": 0.05,
    }
    assert MECHANISM_IDS == (
        "cross_sectional_momentum",
        "pullback_continuation",
        "liquidity_volatility_compression",
        "btc_relative_strength",
    )
    assert GEOMETRY_IDS == (
        "quick_075atr_050r_3d",
        "quick_100atr_075r_5d",
        "quick_125atr_100r_7d",
    )
    assert CONTROL_IDS == ("inverse", "shuffled", "random_coin", "random_timing")
    assert asdict(DEFAULT_CONTRACT) == expected
    assert DEFAULT_CONTRACT == CryptoMechanismContract()

    with pytest.raises(FrozenInstanceError):
        DEFAULT_CONTRACT.minimum_accuracy = 0.79  # type: ignore[misc]

    for field in fields(DEFAULT_CONTRACT):
        value = getattr(DEFAULT_CONTRACT, field.name)
        if isinstance(value, tuple):
            replacement = (*value, "not-frozen")
        elif isinstance(value, str):
            replacement = f"{value}-not-frozen"
        elif isinstance(value, int):
            replacement = value + 1
        else:
            replacement = value + 0.01
        with pytest.raises(ValueError):
            replace(DEFAULT_CONTRACT, **{field.name: replacement})


def test_feature_panel_is_outcome_free_and_strictly_causal() -> None:
    daily = _daily_history(session_count=31)
    cutoff = pd.Timestamp("2026-01-30", tz="UTC")

    through_cutoff = build_feature_panel(daily.loc[daily["session"] <= cutoff].copy())
    with_future = daily.copy()
    future = with_future["session"] > cutoff
    with_future.loc[future, ["open", "high", "low", "close", "volume"]] *= 1000.0
    full_panel = build_feature_panel(with_future)
    causal_prefix = full_panel.loc[
        pd.to_datetime(full_panel["decision_session"], utc=True) <= cutoff
    ]

    assert FORBIDDEN_OUTCOME_COLUMNS.isdisjoint(through_cutoff.columns)
    pd.testing.assert_frame_equal(
        _sort_features(through_cutoff),
        _sort_features(causal_prefix),
        check_like=False,
    )


def test_feature_selections_are_stable_under_input_reordering() -> None:
    daily = _daily_history()
    first = build_feature_panel(daily)
    reordered = build_feature_panel(daily.sample(frac=1.0, random_state=941).reset_index(drop=True))
    columns = [
        "scrip_code",
        "decision_session",
        "mechanism",
        "score",
        "score_rank_pct",
        "pool_eligible",
        "selected",
        "inverse_selected",
    ]

    pd.testing.assert_frame_equal(
        _sort_features(first)[columns],
        _sort_features(reordered)[columns],
        check_like=False,
    )
    final_session = pd.to_datetime(first["decision_session"], utc=True).max()
    final_ready = first.loc[
        (pd.to_datetime(first["decision_session"], utc=True) == final_session)
        & (first["feature_status"] == "eligible")
    ]
    assert set(final_ready["mechanism"]) == set(MECHANISM_IDS)
    assert final_ready.groupby("mechanism")["selected"].sum().gt(0).all()


def test_calendar_gap_resets_only_the_affected_pair_history() -> None:
    daily = _daily_history()
    sessions = sorted(daily["session"].unique())
    gapped_pair = "COIN000USDT"
    daily = daily.loc[
        ~(
            (daily["scrip_code"] == gapped_pair)
            & (daily["session"] == sessions[-2])
        )
    ].copy()

    panel = build_feature_panel(daily)
    final_session = pd.Timestamp(sessions[-1])
    final_rows = panel.loc[
        (panel["scrip_code"] == gapped_pair)
        & (pd.to_datetime(panel["decision_session"], utc=True) == final_session)
    ]

    assert set(final_rows["mechanism"]) == set(MECHANISM_IDS)
    assert (final_rows["feature_status"] == "excluded").all()
    assert (final_rows["exclusion_reason"] == "feature_unavailable").all()
    assert final_rows["registered_active_count"].eq(210).all()
    assert final_rows["feature_valid_count"].ge(200).all()
    assert final_rows["cross_section_coverage"].ge(0.90).all()


def test_label_join_rejects_duplicate_feature_identity() -> None:
    features, labels = _join_inputs()
    duplicated = pd.concat([features, features.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="duplicate pair/decision/mechanism"):
        join_frozen_labels(duplicated, labels)


def test_label_join_rejects_duplicate_label_identity() -> None:
    features, labels = _join_inputs()
    duplicated = pd.concat([labels, labels.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="duplicate pair/decision/geometry"):
        join_frozen_labels(features, duplicated)


def test_label_join_rejects_missing_or_unregistered_geometry() -> None:
    features, labels = _join_inputs()

    with pytest.raises(ValueError, match="feature population and C1 geometry labels differ"):
        join_frozen_labels(features, labels.iloc[:-1].copy())

    unknown = labels.copy()
    unknown.loc[unknown.index[0], "geometry"] = "unregistered_geometry"
    with pytest.raises(ValueError, match="unregistered geometries"):
        join_frozen_labels(features, unknown)


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_OUTCOME_COLUMNS))
def test_label_join_rejects_outcomes_in_feature_artifact(forbidden: str) -> None:
    features, labels = _join_inputs()
    features[forbidden] = 0

    with pytest.raises(ValueError, match="feature artifact contains forbidden outcome columns"):
        join_frozen_labels(features, labels)


def test_no_sample_metrics_are_none_instead_of_zero_evidence() -> None:
    frame = pd.DataFrame(
        {
            "scrip_code": ["COIN000USDT"],
            "decision_session": [pd.Timestamp("2026-01-30", tz="UTC")],
            "label_status": ["resolved"],
            "label": [1],
            "gross_r": [0.5],
            "net_r": [0.4],
            "after_tax_r": [0.3],
        }
    )
    result = mechanism_metrics(frame, np.zeros(len(frame), dtype=bool))

    assert result["attempts"] == 0
    assert result["resolved_calls"] == 0
    assert result["wins"] == 0
    assert result["active_sessions"] == 0
    for metric in (
        "observed_accuracy",
        "wilson95_lower",
        "mean_gross_r",
        "mean_net_r",
        "mean_after_tax_r",
        "maximum_coin_share",
    ):
        assert result[metric] is None


def test_control_assignments_are_deterministic_and_label_independent() -> None:
    mechanism_frame = _control_feature_frame()

    assignments, assignment_sha = _control_indices(mechanism_frame, DEFAULT_CONTRACT)
    changed_outcomes = mechanism_frame.copy()
    changed_outcomes["label"] = np.arange(len(changed_outcomes)) % 2
    changed_outcomes["gross_r"] = np.linspace(-50.0, 50.0, len(changed_outcomes))
    changed_outcomes["net_r"] = np.linspace(999.0, -999.0, len(changed_outcomes))
    changed_outcomes["after_tax_r"] = np.nan
    repeated, repeated_sha = _control_indices(changed_outcomes, DEFAULT_CONTRACT)

    stochastic_control_ids = set(CONTROL_IDS) - {"inverse"}
    assert set(assignments) == stochastic_control_ids
    assert assignment_sha == repeated_sha
    for control_id in stochastic_control_ids:
        np.testing.assert_array_equal(assignments[control_id], repeated[control_id])


def test_full_race_evaluates_exactly_twelve_trials_and_rejects_weak_signal() -> None:
    features = build_feature_panel(_daily_history(pair_count=200, session_count=30))
    identities = features[["scrip_code", "decision_session"]].drop_duplicates()
    label_rows: list[dict[str, object]] = []
    for row in identities.itertuples(index=False):
        code = str(row.scrip_code)
        coin_number = int(code[4:7]) if code.startswith("COIN") else -1
        won = int(coin_number >= 0 and coin_number % 5 == 0)
        for geometry in GEOMETRY_IDS:
            label_rows.append(
                {
                    "scrip_code": code,
                    "decision_session": row.decision_session,
                    "geometry": geometry,
                    "point_in_time_eligible": True,
                    "status": "resolved",
                    "label": won,
                    "gross_r": 0.5 if won else -1.0,
                    "net_r": 0.4 if won else -1.1,
                    "after_tax_r": 0.2 if won else -1.1,
                }
            )

    trials, controls, summary = evaluate_mechanism_race(
        features, pd.DataFrame(label_rows)
    )

    assert len(trials) == 12
    assert summary["registered_trials"] == 12
    assert summary["evaluated_trials"] == 12
    assert summary["passing_trials"] == 0
    assert summary["nominee"] is None
    assert {row["status"] for row in trials} == {"rejected_stopped"}
    assert len(controls) == 12 * 3 * DEFAULT_CONTRACT.cohort_repetitions


def _terminal_report(output: Path, status: str) -> dict:
    passed = 1 if status == "mechanism_race_passed_research_only" else 0
    report = {
        "version": VERSION,
        "id": "terminal-run",
        "status": status,
        "created_at": "2026-11-05T00:00:00+00:00",
        "c1_readiness": {
            "minimum_pair_sessions": 30,
            "required_pair_sessions": 30,
            "ready_pairs": 337,
            "required_pairs": 337,
            "development_window_mature": True,
        },
        "trial_counts": {
            "registered": 12,
            "evaluated": 12,
            "passed": passed,
            "rejected": 12 - passed,
            "incomplete": 0,
        },
        "best_trial": {"mechanism": "cross_sectional_momentum"} if passed else None,
        "source_integrity": {"passed": True, "errors": []},
        "evidence_scope": "crypto_only_never_pooled_with_nse_or_bse",
        "first_look_latched": True,
        "baseline_improved": False,
        "eligible_for_live": False,
    }
    run = output / "runs" / "terminal-run"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(report, sort_keys=True), encoding="utf-8"
    )
    return report


@pytest.mark.parametrize(
    "status",
    ("mechanism_race_rejected", "mechanism_race_passed_research_only"),
)
def test_first_terminal_crypto_decision_survives_mutable_state_and_missing_c1(
    monkeypatch, tmp_path, status: str
) -> None:
    output = tmp_path / "c2"
    report = _terminal_report(output, status)
    monkeypatch.setattr(
        mechanisms_module,
        "verify_crypto_accuracy_mechanisms",
        lambda *_args, **_kwargs: {"passed": True, "errors": []},
    )
    latched = _latch_terminal(output, report)
    terminal_bytes = (output / "terminal-first-look.json").read_bytes()
    (output / "state.json").write_text(
        json.dumps({"status": "mechanism_race_rejected"}), encoding="utf-8"
    )

    repeated = run_crypto_accuracy_mechanisms(
        c1_output=tmp_path / "missing-c1",
        c1_db_path=tmp_path / "missing.duckdb",
        output=output,
    )

    assert repeated == latched == report
    assert (output / "terminal-first-look.json").read_bytes() == terminal_bytes


def test_crypto_terminal_tamper_fails_closed_before_reopening_c1(
    monkeypatch, tmp_path
) -> None:
    output = tmp_path / "c2"
    report = _terminal_report(output, "mechanism_race_rejected")
    monkeypatch.setattr(
        mechanisms_module,
        "verify_crypto_accuracy_mechanisms",
        lambda *_args, **_kwargs: {"passed": True, "errors": []},
    )
    _latch_terminal(output, report)
    path = output / "terminal-first-look.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["report"]["status"] = "mechanism_race_passed_research_only"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ValueError, match="terminal artifact failed verification"):
        run_crypto_accuracy_mechanisms(
            c1_output=tmp_path / "missing-c1",
            c1_db_path=tmp_path / "missing.duckdb",
            output=output,
        )


def test_crypto_terminal_publish_is_atomic_when_replace_fails(monkeypatch, tmp_path) -> None:
    output = tmp_path / "c2"
    report = _terminal_report(output, "mechanism_race_rejected")

    def fail_replace(_source, _target) -> None:
        raise OSError("simulated interrupted publish")

    monkeypatch.setattr(mechanisms_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="interrupted publish"):
        _latch_terminal(output, report)

    assert not (output / "terminal-first-look.json").exists()
    assert list(output.glob(".terminal-first-look.json.*.tmp")) == []


def test_crypto_terminal_is_bound_to_its_immutable_run_manifest(
    monkeypatch, tmp_path
) -> None:
    output = tmp_path / "c2"
    report = _terminal_report(output, "mechanism_race_rejected")
    monkeypatch.setattr(
        mechanisms_module,
        "verify_crypto_accuracy_mechanisms",
        lambda *_args, **_kwargs: {"passed": True, "errors": []},
    )
    _latch_terminal(output, report)
    manifest_path = output / "runs" / "terminal-run" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["trial_counts"]["passed"] = 12
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="terminal_manifest_identity"):
        _verified_terminal(output)


def test_public_crypto_terminal_verifier_normalizes_unexpected_errors(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        mechanisms_module,
        "_verified_terminal",
        lambda _output: (_ for _ in ()).throw(AttributeError("malformed manifest")),
    )

    with pytest.raises(ValueError, match="terminal artifact failed verification"):
        mechanisms_module.verify_crypto_terminal(tmp_path)
