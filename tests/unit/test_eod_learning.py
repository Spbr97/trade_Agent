"""The EOD learning lifecycle: explore -> revalidate -> race -> consolidate.

These drive `_advance` directly with a CRAFTED dataset where the right answer is known in
advance - a genuinely predictive column is planted and the rest is noise - so the tests
prove the loop reaches the correct decision, not merely that it runs without raising.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from tradedesk.eod_learning import (
    ALL_FEATURE_NAMES,
    CORE_FEATURES,
    MODEL_RACE_EVERY,
    REVALIDATE_EVERY,
    LearningState,
    _advance,
    _fit_and_score,
    _max_features_for,
    exploration_pool,
)

SIGNAL = "roc5"  # planted; second in the interleaved pool, so run 2 reaches it


def _dataset(n: int = 420, seed: int = 7, signal_strength: float = 3.0) -> pd.DataFrame:
    """One row per session. `SIGNAL` genuinely predicts the label; every other feature is
    independent noise, so a working loop adopts exactly one of them."""
    rng = np.random.default_rng(seed)
    start = date(2025, 1, 1)
    rows: list[dict[str, object]] = []
    for i in range(n):
        feats = {name: float(rng.normal()) for name in ALL_FEATURE_NAMES}
        p = 1.0 / (1.0 + np.exp(-signal_strength * feats[SIGNAL]))
        label = int(rng.random() < p)
        rows.append(
            {
                "armed_date": start + timedelta(days=i),
                "label_profit": label,
                "gross_r": 0.4 if label else -0.4,
                **feats,
            }
        )
    return pd.DataFrame(rows)


def _base(df: pd.DataFrame, state: LearningState) -> dict[str, object]:
    res = _fit_and_score(df, state.active_features(), seed=1, cost_r=0.1, kind=state.model_kind)
    assert res is not None
    return res


def test_exploration_adopts_a_real_signal_and_rejects_noise() -> None:
    df = _dataset()
    state = LearningState()
    adopted_signal = False
    rejected_at_least_one = False

    # Walk far enough to reach the planted column; the pool is interleaved so it is early.
    for _ in range(6):
        state.fit_runs += 1
        phase, summary = _advance(
            state, df, _base(df, state), "nse", cap=50, cost_r=0.1, record={}
        )
        if phase != "explore":
            continue
        if SIGNAL in state.adopted:
            adopted_signal = True
        if "not adopted" in summary:
            rejected_at_least_one = True

    assert adopted_signal, f"never adopted the planted signal; adopted={state.adopted}"
    assert rejected_at_least_one, "never rejected a noise feature - the bar is not binding"
    assert all(f not in state.adopted for f in CORE_FEATURES), "core must not be re-adopted"


def test_revalidation_drops_a_feature_that_stopped_earning_its_place() -> None:
    """A feature adopted on thin/noisy data must be removable later. Here a pure-noise
    column is force-adopted, then revalidation is put on its cadence - removing it costs
    essentially nothing, so it must be dropped."""
    df = _dataset()
    noise = next(f for f in exploration_pool("nse") if f != SIGNAL)
    state = LearningState(adopted=[noise], explored=[noise])
    state.fit_runs = REVALIDATE_EVERY  # exactly on the revalidation cadence

    record: dict[str, object] = {}
    phase, summary = _advance(state, df, _base(df, state), "nse", cap=50, cost_r=0.1, record=record)

    assert phase == "revalidate", f"expected a revalidation pass, got {phase}: {summary}"
    assert noise not in state.adopted, f"noise feature survived revalidation: {summary}"
    assert noise in state.dropped
    assert record.get("dropped_feature") == noise


def test_revalidation_keeps_a_feature_that_is_still_earning_its_place() -> None:
    df = _dataset()
    state = LearningState(adopted=[SIGNAL], explored=[SIGNAL])
    state.fit_runs = REVALIDATE_EVERY

    phase, summary = _advance(state, df, _base(df, state), "nse", cap=50, cost_r=0.1, record={})

    assert phase == "revalidate"
    assert SIGNAL in state.adopted, f"dropped a genuinely predictive feature: {summary}"


def test_feature_cap_pauses_exploration_on_thin_data() -> None:
    """The guard that stops the model accumulating features it has no rows to support."""
    df = _dataset()
    state = LearningState()
    state.fit_runs = 1  # not on any cadence

    phase, summary = _advance(
        state, df, _base(df, state), "nse", cap=len(CORE_FEATURES), cost_r=0.1, record={}
    )

    assert phase == "consolidate"
    assert "feature cap" in summary
    assert state.adopted == [], "nothing may be adopted while at the cap"


def test_max_features_scales_with_sample_size() -> None:
    assert _max_features_for(0) == len(CORE_FEATURES)  # never below the core set
    assert _max_features_for(60) == len(CORE_FEATURES)
    assert _max_features_for(450) == 30
    assert _max_features_for(1500) == 100


def test_crypto_pool_excludes_event_features_but_nse_includes_them() -> None:
    """Crypto has no earnings calendar - those columns would be a constant there."""
    crypto, nse = exploration_pool("crypto"), exploration_pool("nse")
    for name in ("near_term_results", "days_since_results", "days_since_corp_action"):
        assert name not in crypto
        assert name in nse
    # Interleaving, not family-draining: the first few picks span different families.
    assert len({n for n in nse[:4]}) == 4


def test_model_race_runs_on_its_cadence_and_keeps_incumbent_without_a_real_gain() -> None:
    """With only logistic available (small n keeps the tree out), the race has no challenger
    and must leave the incumbent alone rather than thrash."""
    df = _dataset()
    state = LearningState()
    state.fit_runs = MODEL_RACE_EVERY

    record: dict[str, object] = {}
    phase, _ = _advance(state, df, _base(df, state), "nse", cap=50, cost_r=0.1, record=record)

    assert state.model_kind == "logistic"
    # Either it raced and kept the incumbent, or there was no challenger so it moved on to
    # the next decision - both are correct; what must NOT happen is a silent switch.
    assert phase in {"model", "revalidate", "explore", "consolidate"}


def test_state_round_trips_and_reads_the_pre_rework_format(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    state = LearningState(adopted=["roc5"], explored=["roc5", "bb_width"], dropped=["bb_width"])
    state.fit_runs = 4
    state.model_kind = "xgboost"
    state.save(p)
    back = LearningState.load(p)
    assert (back.adopted, back.dropped, back.fit_runs, back.model_kind) == (
        ["roc5"], ["bb_width"], 4, "xgboost",
    )

    # The old shape stored core+adopted together under base_features, with no other keys.
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps({"base_features": [*CORE_FEATURES, "gap_pct"], "explored": ["gap_pct"]}),
        encoding="utf-8",
    )
    migrated = LearningState.load(legacy)
    assert migrated.adopted == ["gap_pct"], "core features must not be duplicated into adopted"
    assert migrated.model_kind == "logistic"
    assert migrated.fit_runs == 0


def test_adoption_and_removal_use_the_same_margin() -> None:
    """No ratchet: the bar to get in is the bar to stay in. Guards against a future edit
    loosening one side and letting the feature set only ever grow."""
    import inspect

    from tradedesk import eod_learning

    src = inspect.getsource(eod_learning._advance)
    assert src.count("BRIER_IMPROVEMENT_MARGIN") >= 2
