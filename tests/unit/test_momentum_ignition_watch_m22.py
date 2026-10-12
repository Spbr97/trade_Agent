from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import tradedesk.momentum_ignition_watch_m22 as m22_module
from tradedesk.momentum_ignition_watch import (
    build_momentum_ignition_watch,
    collect_momentum_ignition_watch,
)
from tradedesk.momentum_ignition_watch_m22 import (
    FEED_WINDOW_BARS,
    VERSION,
    _feed_invalid_reason,
    build_momentum_ignition_watch_m22,
    collect_momentum_ignition_watch_m22,
    load_momentum_ignition_watch_m22,
)

OBSERVED_AT = datetime(2026, 10, 12, 12, 45, tzinfo=UTC)
ACTIVATION_COUNT = 339


def _codes(count: int) -> list[str]:
    return [f"CDX_COIN{index:03d}INR" for index in range(count)]


def _shortlist(count: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "scrip_code": code,
                "symbol": code.removeprefix("CDX_"),
                "opportunity_probability": 0.5 + index / 10_000.0,
            }
            for index, code in enumerate(_codes(count))
        ]
    )


def _activation(count: int = ACTIVATION_COUNT) -> dict[str, object]:
    return {
        "codes": _codes(count),
        "activation_count": count,
        "manifest_sha256": "test-activation-manifest",
    }


def _valid_frame(strength: int = 0) -> pd.DataFrame:
    index = pd.date_range(
        end=pd.Timestamp(OBSERVED_AT) - pd.Timedelta(minutes=15),
        periods=FEED_WINDOW_BARS,
        freq="15min",
        tz=UTC,
    )
    close = np.full(FEED_WINDOW_BARS, 100.0)
    close[-1] = 100.05 + strength * 0.004
    high = close + 1.0
    low = close - 1.0
    high[-1] = close[-1] + 0.8
    low[-1] = close[-1] - 1.6
    volume = np.full(FEED_WINDOW_BARS, 100.0)
    volume[-1] = 160.0 + strength * 0.2
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def _zero_volume_frame() -> pd.DataFrame:
    frame = _valid_frame()
    frame.loc[:, "volume"] = 0.0
    return frame


def _zero_range_frame() -> pd.DataFrame:
    frame = _valid_frame()
    frame.loc[:, ["open", "high", "low", "close"]] = 100.0
    return frame


def _gate_report(
    monkeypatch: pytest.MonkeyPatch,
    *,
    requested: int = ACTIVATION_COUNT,
    feed_valid: int,
    feature_valid: int,
) -> dict[str, object]:
    if not 0 <= feature_valid <= feed_valid <= requested:
        raise ValueError("invalid test gate population")
    shortlist = _shortlist(requested)
    codes = _codes(requested)
    feature_frame = _valid_frame()
    inactive_frame = _zero_volume_frame()
    frames = {
        code: feature_frame if index < feature_valid else inactive_frame
        for index, code in enumerate(codes[:feed_valid])
    }

    # The feed and feature validators have their own exact tests above. Isolate the
    # population arithmetic here so six 300+ pair boundary cases remain fast and do not
    # repeatedly exercise M21's comparatively expensive per-candidate economics.
    monkeypatch.setattr(
        m22_module,
        "_feed_invalid_reason",
        lambda source, *, observed_at: (
            None if isinstance(source, pd.DataFrame) else "incomplete_96_bar_window"
        ),
    )
    monkeypatch.setattr(m22_module, "_frame", lambda source, observed_at: source)
    monkeypatch.setattr(
        m22_module,
        "_feature_invalid_reason",
        lambda frame: (
            None
            if float(frame["volume"].sum()) > 0.0
            else "nonpositive_prior_volume20"
        ),
    )

    def inherited_stub(
        inherited_shortlist: pd.DataFrame,
        inherited_frames: object,
        *,
        observed_at: datetime,
        plan_history: object = (),
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        del inherited_frames, plan_history
        return (
            {
                "version": "momentum-ignition-watch-v1",
                "market": "crypto",
                "observed_at": observed_at.isoformat(),
                "status": "no_momentum_ignition_candidate",
                "timeframe": "M15_completed_bars_only",
                "shortlist_rows": len(inherited_shortlist),
                "intraday_valid_rows": len(inherited_shortlist),
                "intraday_invalid_rows": 0,
                "universe_coverage": 1.0,
                "minimum_universe_coverage": 0.85,
                "registration_candidates": 0,
                "registration_candidate_pool": 0,
                "maximum_new_registrations_per_snapshot": 3,
                "watch_candidates": 0,
                "ignition_candidates": 0,
                "reclaim_candidates": 0,
                "tradeable_calls": 0,
                "candidates": [],
                "authority": "research_watch_only",
                "eligible_for_live": False,
                "baseline_accuracy_improved": False,
                "detail": "test stub",
            },
            [],
        )

    monkeypatch.setattr(
        m22_module,
        "build_momentum_ignition_watch",
        inherited_stub,
    )
    report, _ = build_momentum_ignition_watch_m22(
        shortlist,
        frames,
        observed_at=OBSERVED_AT,
        activation=_activation(),
    )
    return report


def test_feed_validity_requires_exact_causal_gap_free_96_bar_window() -> None:
    valid = _valid_frame()
    assert _feed_invalid_reason(valid, observed_at=OBSERVED_AT) is None

    assert (
        _feed_invalid_reason(valid.iloc[1:], observed_at=OBSERVED_AT)
        == "incomplete_96_bar_window"
    )

    duplicate = valid.copy()
    duplicate.index = pd.DatetimeIndex([*valid.index[:-1], valid.index[-2]])
    assert (
        _feed_invalid_reason(duplicate, observed_at=OBSERVED_AT)
        == "duplicate_timestamp"
    )

    stale = valid.copy()
    stale.index = stale.index - pd.Timedelta(minutes=15)
    assert (
        _feed_invalid_reason(stale, observed_at=OBSERVED_AT)
        == "stale_gapped_or_misaligned_window"
    )

    gapped = valid.copy()
    shifted = list(gapped.index)
    shifted[40] = shifted[40] + pd.Timedelta(minutes=1)
    gapped.index = pd.DatetimeIndex(shifted)
    assert (
        _feed_invalid_reason(gapped, observed_at=OBSERVED_AT)
        == "stale_gapped_or_misaligned_window"
    )

    forming = valid.copy()
    forming.index = forming.index + pd.Timedelta(minutes=15)
    assert (
        _feed_invalid_reason(forming, observed_at=OBSERVED_AT)
        == "incomplete_96_bar_window"
    )


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("volume", np.inf, "non_finite_ohlcv"),
        ("open", 0.0, "non_positive_ohlc"),
        ("volume", -1.0, "negative_volume"),
    ],
)
def test_feed_validity_rejects_malformed_ohlcv(
    column: str, value: float, reason: str
) -> None:
    frame = _valid_frame()
    frame.loc[frame.index[-1], column] = value

    assert _feed_invalid_reason(frame, observed_at=OBSERVED_AT) == reason


def test_feed_validity_rejects_invalid_ohlc_geometry() -> None:
    frame = _valid_frame()
    frame.loc[frame.index[-1], "high"] = frame.iloc[-1]["low"] - 1.0

    assert (
        _feed_invalid_reason(frame, observed_at=OBSERVED_AT)
        == "invalid_ohlc_geometry"
    )


def test_inactivity_is_feed_valid_but_feature_invalid_and_never_ranked() -> None:
    shortlist = _shortlist(ACTIVATION_COUNT)
    codes = _codes(ACTIVATION_COUNT)
    shared = _valid_frame()
    frames = {code: shared for code in codes}
    frames[codes[-2]] = _zero_volume_frame()
    frames[codes[-1]] = _zero_range_frame()

    assert _feed_invalid_reason(frames[codes[-2]], observed_at=OBSERVED_AT) is None
    assert _feed_invalid_reason(frames[codes[-1]], observed_at=OBSERVED_AT) is None

    report, registration = build_momentum_ignition_watch_m22(
        shortlist,
        frames,
        observed_at=OBSERVED_AT,
        activation=_activation(),
    )

    assert report["feed_valid_pairs"] == ACTIVATION_COUNT
    assert report["feed_invalid_pairs"] == 0
    assert report["feature_valid_pairs"] == ACTIVATION_COUNT - 2
    assert report["feature_invalid_pairs"] == 2
    assert report["feature_invalid_reason_counts"] == {
        "nonpositive_atr14": 1,
        "nonpositive_prior_volume20": 1,
    }
    inactive = {codes[-2], codes[-1]}
    assert inactive.isdisjoint(
        str(candidate["scrip_code"]) for candidate in report["candidates"]
    )
    assert inactive.isdisjoint(
        str(candidate["scrip_code"]) for candidate in registration
    )


@pytest.mark.parametrize(
    ("feed_valid", "expected"),
    [(322, False), (323, True)],
)
def test_feed_gate_has_an_exact_95_percent_boundary(
    monkeypatch: pytest.MonkeyPatch, feed_valid: int, expected: bool
) -> None:
    report = _gate_report(
        monkeypatch,
        feed_valid=feed_valid,
        feature_valid=feed_valid,
    )

    # Feature-invalid counts are stage-local. Feed failures remain visible in the
    # end-to-end intraday-invalid count but cannot be double-counted as feature failures.
    assert report["feature_invalid_pairs"] == 0
    assert report["feature_invalid_rows"] == 0
    assert report["intraday_invalid_rows"] == ACTIVATION_COUNT - feed_valid
    assert report["feed_gate_passed"] is expected
    assert report["coverage_gate_passed"] is expected
    assert ("insufficient_feed_coverage" in report["coverage_blockers"]) is not expected


@pytest.mark.parametrize(
    ("feature_valid", "expected"),
    [(203, False), (204, True)],
)
def test_feature_gate_requires_200_rows_and_60_percent_of_feed(
    monkeypatch: pytest.MonkeyPatch, feature_valid: int, expected: bool
) -> None:
    report = _gate_report(
        monkeypatch,
        feed_valid=ACTIVATION_COUNT,
        feature_valid=feature_valid,
    )

    assert report["feed_gate_passed"] is True
    assert report["feature_gate_passed"] is expected
    assert report["coverage_gate_passed"] is expected
    assert ("insufficient_feature_breadth" in report["coverage_blockers"]) is not expected


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(305, False), (306, True)],
)
def test_manifest_gate_requires_90_percent_of_activation_population(
    monkeypatch: pytest.MonkeyPatch, requested: int, expected: bool
) -> None:
    report = _gate_report(
        monkeypatch,
        requested=requested,
        feed_valid=requested,
        feature_valid=requested,
    )

    assert report["manifest_gate_passed"] is expected
    assert report["coverage_gate_passed"] is expected
    assert ("activation_universe_drift" in report["coverage_blockers"]) is not expected


def test_m22_preserves_m21_signal_ranking_geometry_and_economics() -> None:
    shortlist = _shortlist(ACTIVATION_COUNT)
    frames = {
        code: _valid_frame(index)
        for index, code in enumerate(_codes(ACTIVATION_COUNT))
    }

    m21, m21_registration = build_momentum_ignition_watch(
        shortlist,
        frames,
        observed_at=OBSERVED_AT,
    )
    m22, m22_registration = build_momentum_ignition_watch_m22(
        shortlist,
        frames,
        observed_at=OBSERVED_AT,
        activation=_activation(),
    )

    assert m22["coverage_gate_passed"] is True
    assert m22["candidates"] == m21["candidates"]
    assert m22_registration == m21_registration
    assert m22["watch_candidates"] == m21["watch_candidates"]
    assert m22["ignition_candidates"] == m21["ignition_candidates"]
    assert m22["reclaim_candidates"] == m21["reclaim_candidates"]


def test_m22_persistence_and_loading_are_independent_of_m21(tmp_path: Path) -> None:
    shortlist = _shortlist(ACTIVATION_COUNT)
    frames = {
        code: _valid_frame(index)
        for index, code in enumerate(_codes(ACTIVATION_COUNT))
    }
    m21_root = tmp_path / "m21"
    m22_root = tmp_path / "m22"

    collect_momentum_ignition_watch(
        shortlist,
        frames,
        observed_at=OBSERVED_AT,
        output_root=m21_root,
    )
    m21_latest = (m21_root / "crypto" / "latest.json").read_bytes()
    m21_snapshots = (m21_root / "crypto" / "snapshots.jsonl").read_bytes()
    m21_plans = (m21_root / "crypto" / "plans.jsonl").read_bytes()

    collected = collect_momentum_ignition_watch_m22(
        shortlist,
        frames,
        observed_at=OBSERVED_AT,
        output_root=m22_root,
        source={"shared_m15_fetch_with_m21": True},
    )
    loaded = load_momentum_ignition_watch_m22(
        "crypto",
        output_root=m22_root,
        observed_at=OBSERVED_AT,
    )

    assert collected["version"] == VERSION
    assert loaded["version"] == VERSION
    assert loaded["snapshot_sha256"] == collected["snapshot_sha256"]
    assert (m22_root / "crypto" / "activation.json").exists()
    assert (m22_root / "crypto" / "latest.json").exists()
    assert (m22_root / "crypto" / "snapshots.jsonl").exists()
    assert (m22_root / "crypto" / "plans.jsonl").exists()
    assert (m21_root / "crypto" / "latest.json").read_bytes() == m21_latest
    assert (m21_root / "crypto" / "snapshots.jsonl").read_bytes() == m21_snapshots
    assert (m21_root / "crypto" / "plans.jsonl").read_bytes() == m21_plans
    assert not (m21_root / "crypto" / "activation.json").exists()

    m21_events = [
        json.loads(line)
        for line in m21_plans.decode("utf-8").splitlines()
        if line
    ]
    m22_events = [
        json.loads(line)
        for line in (m22_root / "crypto" / "plans.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert m21_events
    assert m22_events
    assert {str(event["version"]) for event in m21_events} == {
        "momentum-ignition-plan-v1"
    }
    assert {str(event["version"]) for event in m22_events} == {
        "momentum-ignition-plan-m22-v1"
    }
    assert {str(event["plan_id"]) for event in m21_events}.isdisjoint(
        str(event["plan_id"]) for event in m22_events
    )


def test_m22_loader_fails_closed_when_its_own_snapshot_is_tampered(
    tmp_path: Path,
) -> None:
    shortlist = _shortlist(1)
    frame = _valid_frame()
    frames = {_codes(1)[0]: frame}
    output_root = tmp_path / "m22"
    collect_momentum_ignition_watch_m22(
        shortlist,
        frames,
        observed_at=OBSERVED_AT,
        output_root=output_root,
    )
    path = output_root / "crypto" / "latest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "qualified"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        load_momentum_ignition_watch_m22(
            "crypto",
            output_root=output_root,
            observed_at=OBSERVED_AT + timedelta(minutes=15),
        )


def test_m22_rejects_a_valid_but_misplaced_m21_plan_chain(tmp_path: Path) -> None:
    output_root = tmp_path / "m22"
    plan_path = output_root / "crypto" / "plans.jsonl"
    m22_module._append_hash_event(
        plan_path,
        {
            "version": "momentum-ignition-plan-v1",
            "event": "plan_armed",
            "plan_id": "foreign-m21-plan",
            "market": "crypto",
            "state": "pending_trigger",
            "armed_at": OBSERVED_AT.isoformat(),
        },
        label="test foreign plan",
    )

    with pytest.raises(ValueError, match="M22 plan namespace mismatch"):
        collect_momentum_ignition_watch_m22(
            _shortlist(1),
            {_codes(1)[0]: _valid_frame()},
            observed_at=OBSERVED_AT,
            output_root=output_root,
        )


def test_m22_rejects_a_plan_armed_before_activation(tmp_path: Path) -> None:
    output_root = tmp_path / "m22"
    collect_momentum_ignition_watch_m22(
        _shortlist(1),
        {_codes(1)[0]: _valid_frame()},
        observed_at=OBSERVED_AT,
        output_root=output_root,
    )
    (output_root / "crypto" / "latest.json").unlink()
    (output_root / "crypto" / "snapshots.jsonl").unlink()
    plan_path = output_root / "crypto" / "plans.jsonl"
    m22_module._append_hash_event(
        plan_path,
        {
            "version": "momentum-ignition-plan-m22-v1",
            "event": "plan_armed",
            "plan_id": "pre-activation-plan",
            "market": "crypto",
            "state": "pending_trigger",
            "armed_at": (OBSERVED_AT - timedelta(minutes=1)).isoformat(),
        },
        label="test pre-activation plan",
    )

    with pytest.raises(ValueError, match="M22 plan predates activation"):
        load_momentum_ignition_watch_m22(
            "crypto", output_root=output_root, observed_at=OBSERVED_AT
        )


def test_m22_rejects_an_unbound_qualification_chain(tmp_path: Path) -> None:
    output_root = tmp_path / "m22"
    collect_momentum_ignition_watch_m22(
        _shortlist(1),
        {_codes(1)[0]: _valid_frame()},
        observed_at=OBSERVED_AT,
        output_root=output_root,
    )
    m22_module._append_hash_event(
        output_root / "crypto" / "qualification.jsonl",
        {
            "version": "pre-takeoff-evidence-v1",
            "event": "qualification_gate_evaluated",
            "market": "crypto",
            "stage": "foreign",
            "status": "collecting_qualification",
            "terminal": False,
            "observed_at": OBSERVED_AT.isoformat(),
        },
        label="test foreign qualification",
    )

    with pytest.raises(ValueError, match="M22 qualification namespace mismatch"):
        load_momentum_ignition_watch_m22(
            "crypto", output_root=output_root, observed_at=OBSERVED_AT
        )
