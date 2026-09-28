import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_events import DEFAULT_EVENT_ENGINE_CONTRACT
from tradedesk_lab.aem_v2_precision_ladder import (
    REGISTERED_FEATURE_NAMES,
    ScoredCandidate,
    calibrated_probability,
    fit_feature_contributions,
    fit_logistic_control,
    fit_oof_calibration,
    hard_veto_reasons,
    score_candidates,
    score_logistic_control,
    select_calls,
)

GEOMETRY = DEFAULT_AEM_V2_CONTRACT.geometries[0]


def _synthetic_frame(n: int, *, seed: int = 11) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    data = {name: rng.normal(0, 1, n) for name in REGISTERED_FEATURE_NAMES}
    frame = pd.DataFrame(data)
    # Real, informative signal concentrated in one feature so the fit has something to
    # find; the rest are pure noise.
    logit = 0.9 * frame["return_5m"].to_numpy()
    prob = 1 / (1 + np.exp(-logit))
    labels = (rng.uniform(0, 1, n) < prob).astype(int)
    session_order = np.repeat(np.arange(n // 10 + 1), 10)[:n]
    return frame, labels, session_order


def _benign_values() -> dict[str, float]:
    values = dict.fromkeys(REGISTERED_FEATURE_NAMES, 0.01)
    values.update(
        modeled_round_trip_cost_pct=0.0005,
        impact_proxy=0.05,
        median_turnover_20d=50_000_000.0,
        causal_level_distance=0.02,
        daily_extension_atr=0.5,
        limit_distance=0.001,
        time_remaining_minutes=30.0,
    )
    return values


def test_training_and_scoring_frames_must_equal_registered_columns_exactly():
    frame, labels, _ = _synthetic_frame(120)
    with pytest.raises(ValueError, match="frame columns must equal exactly"):
        fit_feature_contributions(frame.assign(net_r=1.0), labels)
    with pytest.raises(ValueError, match="frame columns must equal exactly"):
        fit_feature_contributions(frame.drop(columns=["return_5m"]), labels)

    fitted = fit_feature_contributions(frame, labels)
    with pytest.raises(ValueError, match="frame columns must equal exactly"):
        score_candidates(fitted, frame.assign(strict_success=1))


def test_labels_must_be_binary_and_correct_length():
    frame, labels, _ = _synthetic_frame(120)
    with pytest.raises(ValueError, match="binary array"):
        fit_feature_contributions(frame, labels[:-1])
    with pytest.raises(ValueError, match="binary array"):
        fit_feature_contributions(frame, labels * 2)


def test_insufficient_training_rows_fails_closed():
    frame, labels, _ = _synthetic_frame(50)
    with pytest.raises(ValueError, match="insufficient_training_rows"):
        fit_feature_contributions(frame, labels)


def test_fitted_contribution_recovers_the_informative_features_direction():
    frame, labels, _ = _synthetic_frame(400)
    fitted = fit_feature_contributions(frame, labels)
    binning = fitted.feature_bins["return_5m"]
    assert binning.contributions[-1] > binning.contributions[0]
    noise_binning = fitted.feature_bins["chase_pct"]
    assert abs(binning.contributions[-1] - binning.contributions[0]) > abs(
        noise_binning.contributions[-1] - noise_binning.contributions[0]
    )


def test_oof_calibration_never_scores_a_fold_with_a_model_fit_on_that_fold():
    frame, labels, session_order = _synthetic_frame(360)
    result = fit_oof_calibration(frame, labels, session_order, n_inner_folds=3)

    for fold_id in sorted(set(result.fold_of_row.tolist())):
        test_mask = result.fold_of_row == fold_id
        train_mask = ~test_mask
        independent_fit = fit_feature_contributions(frame.loc[train_mask], labels[train_mask])
        independent_scores = score_candidates(independent_fit, frame.loc[test_mask])
        assert np.allclose(result.oof_scores[test_mask], independent_scores)

    probs = calibrated_probability(result.calibrator, result.oof_scores)
    assert np.all((probs >= 0) & (probs <= 1))


def test_oof_calibration_requires_at_least_two_inner_folds():
    frame, labels, session_order = _synthetic_frame(200)
    with pytest.raises(ValueError, match="at least two inner folds"):
        fit_oof_calibration(frame, labels, session_order, n_inner_folds=1)


def test_hard_veto_reasons_flag_each_registered_condition():
    assert hard_veto_reasons(_benign_values(), mode="anticipatory_impulse", geometry=GEOMETRY) == ()

    too_costly = _benign_values() | {"modeled_round_trip_cost_pct": GEOMETRY.target_pct}
    assert "expected_move_too_small_for_costs" in hard_veto_reasons(
        too_costly, mode="anticipatory_impulse", geometry=GEOMETRY
    )

    illiquid = _benign_values() | {"impact_proxy": 1.0}
    assert "excessive_impact_proxy" in hard_veto_reasons(
        illiquid, mode="anticipatory_impulse", geometry=GEOMETRY
    )

    thin = _benign_values() | {"median_turnover_20d": 1.0}
    assert "inadequate_liquidity" in hard_veto_reasons(
        thin, mode="anticipatory_impulse", geometry=GEOMETRY
    )

    no_room = _benign_values() | {"causal_level_distance": GEOMETRY.target_pct / 2}
    assert "no_remaining_causal_price_room" in hard_veto_reasons(
        no_room, mode="anticipatory_impulse", geometry=GEOMETRY
    )

    extended = _benign_values() | {"daily_extension_atr": 10.0}
    assert "move_already_extended" in hard_veto_reasons(
        extended, mode="anticipatory_impulse", geometry=GEOMETRY
    )

    late = _benign_values() | {"time_remaining_minutes": 0.0}
    assert "past_registered_deadline" in hard_veto_reasons(
        late, mode="anticipatory_impulse", geometry=GEOMETRY
    )


def test_no_remaining_causal_price_room_only_applies_to_anticipatory_impulse():
    """Real bug found 27 September 2026 against the full real dataset: breakout_retest and
    confirmed_pullback opportunities arm AFTER their causal level is already broken (see
    aem_v2_events.py::opportunities_at), so causal_level_distance is structurally negative
    for them by construction, not because the opportunity lacks room to run. The veto must
    not fire for those two modes."""

    no_room = _benign_values() | {"causal_level_distance": -0.05}
    assert "no_remaining_causal_price_room" in hard_veto_reasons(
        no_room, mode="anticipatory_impulse", geometry=GEOMETRY
    )
    for mode in ("breakout_retest", "confirmed_pullback"):
        assert "no_remaining_causal_price_room" not in hard_veto_reasons(
            no_room, mode=mode, geometry=GEOMETRY
        )


def test_entry_requires_excess_chase_veto_was_removed_as_a_tautology():
    """Real bug found 27 September 2026: aem_v2_events.py::opportunities_at computes
    entry_limit as ceil_tick(intended_entry * (1 + maximum_chase_pct)) for every
    opportunity unconditionally, so limit_distance is pinned to maximum_chase_pct plus
    tick-rounding noise for every row - comparing it back against the same constant fired
    on 99.8% of the real dataset regardless of anything real about the opportunity. Real
    chase rejection already happens correctly at fill time in resolve_opportunity's own
    chase_rejected outcome, using the REALIZED fill price - this pre-resolution
    duplicate added no signal and is gone. A pathological limit_distance no longer vetoes
    anything at this layer."""

    chasing = _benign_values() | {
        "limit_distance": DEFAULT_EVENT_ENGINE_CONTRACT.maximum_chase_pct * 10
    }
    assert hard_veto_reasons(chasing, mode="anticipatory_impulse", geometry=GEOMETRY) == ()


def test_select_calls_never_promotes_an_unqualified_candidate_via_rank():
    candidates = [
        ScoredCandidate("A", "S1", "NSE_A", calibrated_probability=0.95, veto_reasons=("vetoed",)),
        ScoredCandidate("B", "S1", "NSE_B", calibrated_probability=0.51),
        ScoredCandidate("C", "S1", "NSE_C", calibrated_probability=0.49),
    ]
    selected = select_calls(candidates, top_k=3)
    assert selected == ("B",)


def test_select_calls_deduplicates_symbols_and_respects_top_k():
    candidates = [
        ScoredCandidate("A1", "S1", "NSE_A", calibrated_probability=0.60),
        ScoredCandidate("A2", "S1", "NSE_A", calibrated_probability=0.90),
        ScoredCandidate("B1", "S1", "NSE_B", calibrated_probability=0.70),
        ScoredCandidate("C1", "S1", "NSE_C", calibrated_probability=0.55),
    ]
    assert select_calls(candidates, top_k=1) == ("A2",)
    assert select_calls(candidates, top_k=2) == ("A2", "B1")


def test_select_calls_rejects_invalid_top_k():
    with pytest.raises(ValueError, match="top_k must be one of"):
        select_calls([], top_k=4)


def test_select_calls_applies_portfolio_filter_before_ranking():
    candidates = [
        ScoredCandidate("A", "S1", "NSE_A", calibrated_probability=0.90),
        ScoredCandidate("B", "S1", "NSE_B", calibrated_probability=0.60),
    ]
    result = select_calls(
        candidates, top_k=1, portfolio_filter=lambda c: c.scrip_code != "NSE_A"
    )
    assert result == ("B",)


def test_logistic_control_rejects_column_mismatch_and_produces_probabilities():
    frame, labels, _ = _synthetic_frame(200)
    with pytest.raises(ValueError, match="frame columns must equal exactly"):
        fit_logistic_control(frame.assign(net_r=1.0), labels)

    model = fit_logistic_control(frame, labels)
    with pytest.raises(ValueError, match="frame columns must equal exactly"):
        score_logistic_control(model, frame.drop(columns=["return_5m"]))
    probs = score_logistic_control(model, frame)
    assert np.all((probs >= 0) & (probs <= 1))
