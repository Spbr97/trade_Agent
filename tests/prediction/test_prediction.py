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
    ModelBundle,
    apply_probability,
    build_dataset,
    drift_check,
    label_signal,
    latest_bundle,
    probability,
    signal_features,
    train,
    triple_barrier,
)
from tradedesk.prediction.features import to_frame
from tradedesk.prediction.predict import log_shadow, read_shadow, score_watchlist
from tradedesk.prediction.train import purged_walk_forward
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


def test_training_recovers_signs_and_is_calibrated(tmp_path: Path) -> None:
    rep = train(synthetic_dataset(), n_splits=4, embargo_sessions=10, prefer_lightgbm=False)
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
