import json
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import forward

from tradedesk.config.models import RiskConfig, Settings


class DummyBundle:
    features = ["dist_ema20_atr"]

    def predict(self, frame):
        return np.full(len(frame), 0.8)


def settings():
    return Settings(risk=RiskConfig(trading_capital=Decimal("100000")))


def signal(armed_on="2026-01-01"):
    return {
        "id": f"trend_pullback:example:{armed_on}",
        "scrip_code": "example",
        "symbol": "EXAMPLE",
        "setup": "trend_pullback",
        "armed_on": armed_on,
        "trigger": 100.0,
        "stop": 95.0,
        "t1": 105.0,
        "t2": 110.0,
        "atr": 2.0,
        "valid_sessions": 3,
        "geometry": {},
    }


def candle_frame(start="2025-01-01", periods=280):
    index = pd.bdate_range(start, periods=periods)
    close = np.linspace(70, 100, periods)
    return pd.DataFrame(
        {
            "open": close - 0.2,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(periods, 100_000),
        },
        index=index,
    )


def test_activation_excludes_every_watchlist_that_already_exists(monkeypatch, tmp_path):
    root = tmp_path / "root"
    output = tmp_path / "lab"
    folder = root / "data/watchlists"
    folder.mkdir(parents=True)
    (folder / "2026-09-15.json").write_text(
        json.dumps({"on": "2026-09-15", "entries": []}), encoding="utf-8"
    )
    report = {"id": "frozen", "metadata": {"to": "2026-09-10"}, "champion": None}
    monkeypatch.setattr(
        forward,
        "_load_frozen",
        lambda output, run_id: (
            report,
            {"a": DummyBundle(), "b": DummyBundle()},
            {"a": "1", "b": "2"},
        ),
    )
    monkeypatch.setattr(forward, "load_config", lambda root: settings())
    state = forward.collect(root, output)
    assert state["activation"]["forward_after"] == "2026-09-15"
    assert state["activation"]["model_data_through"] == "2026-09-10"
    assert state["records"] == []


def test_feature_construction_cannot_see_bars_after_arming():
    bars = candle_frame()
    armed = bars.index[-6].date().isoformat()
    entry = {"signal": signal(armed)}
    watchlist = {"regime": {"breadth_pct": 42, "vix": 14, "vix_change_5d_pct": 2}}
    names = [
        "dist_ema20_atr",
        "rsi14",
        "nr7",
        "breadth_pct",
        "setup_trend_pullback",
    ]
    before = forward.feature_values(entry, watchlist, bars, names)
    poisoned = bars.copy()
    poisoned.loc[poisoned.index.date > pd.Timestamp(armed).date(), "close"] = 1_000_000
    after = forward.feature_values(entry, watchlist, poisoned, names)
    assert before == after


def test_outcome_resolution_changes_only_outcome_fields():
    armed = date = pd.Timestamp("2026-01-01")
    index = pd.bdate_range(date + pd.Timedelta(days=1), periods=12)
    bars = pd.DataFrame(
        {
            "open": [99.0, *([101.0] * 11)],
            "high": [101.0, 106.0, *([103.0] * 10)],
            "low": [98.0, *([99.0] * 11)],
            "close": [100.0, *([102.0] * 11)],
            "volume": [100_000] * 12,
        },
        index=index,
    )
    record = {
        "signal_id": "one",
        "signal": signal(armed.date().isoformat()),
        "qty": 100,
        "contract_version": forward.CONTRACT_VERSION,
        "features_sha256": "frozen-features",
        "probabilities": {"a": 0.7, "b": 0.65},
        "status": "pending_entry",
        "entry_date": None,
        "fill_price": None,
        "outcome": None,
        "label": None,
        "sessions_to_outcome": None,
        "exit_price": None,
        "gross_r": None,
        "resolved_at": None,
        "last_evaluated_candle": None,
    }
    record["prediction_sha256"] = forward._prediction_hash(record)
    frozen = record["prediction_sha256"]
    assert forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "resolved"
    # The real exit plan sells half at T1 and the remainder at the breakeven stop.
    assert record["outcome"] == "stop" and record["label"] == 1
    assert record["target_hit"] and record["strict_success"]
    assert record["net_r"] < record["gross_r"]
    assert record["net_pnl"] > 0 and record["costs"] > 0
    assert forward._prediction_hash(record) == frozen
    assert record["features_sha256"] == "frozen-features"
    assert record["probabilities"] == {"a": 0.7, "b": 0.65}


def test_untriggered_call_expires_without_becoming_a_model_miss():
    index = pd.bdate_range("2026-01-02", periods=5)
    bars = pd.DataFrame(
        {"open": 98.0, "high": 99.0, "low": 97.0, "close": 98.0, "volume": 100_000},
        index=index,
    )
    record = {
        "signal": signal(),
        "status": "pending_entry",
        "last_evaluated_candle": None,
    }
    assert forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "expired"
    assert record.get("label") is None


def test_fourth_session_cannot_enter_after_three_session_validity():
    bars = pd.DataFrame(
        {
            "open": [98, 98, 98, 100],
            "high": [99, 99, 99, 106],
            "low": [97, 97, 97, 99],
            "close": [98, 98, 98, 105],
            "volume": 100_000,
        },
        index=pd.bdate_range("2026-01-02", periods=4),
    )
    record = {"signal": signal(), "status": "pending_entry", "qty": 100}
    forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "expired"
    assert record.get("entry_date") is None
    assert record.get("label") is None


def test_invalid_fill_above_target_is_excluded():
    bars = pd.DataFrame(
        {"open": [101.5], "high": [102.0], "low": [100.0], "close": [101.7], "volume": [100_000]},
        index=pd.to_datetime(["2026-01-02"]),
    )
    sig = signal()
    sig["t1"] = 101.0
    record = {"signal": sig, "status": "pending_entry", "qty": 100}
    forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "invalid_geometry"
    assert record["exclusion_reason"] == "target_at_or_below_fill"
    assert record.get("label") is None


def test_tiny_quantity_target_touch_is_not_after_cost_success():
    bars = pd.DataFrame(
        {
            "open": [100.0, 100.1],
            "high": [100.15, 100.25],
            "low": [99.5, 99.9],
            "close": [100.1, 100.2],
            "volume": 100_000,
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )
    sig = signal()
    sig["t1"] = 100.2
    record = {"signal": sig, "status": "pending_entry", "qty": 1}
    forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "resolved"
    assert record["hypothetical_qty"] == 1
    assert record["target_hit"] is True
    assert record["net_pnl"] < 0 and record["strict_success"] is False
    assert record["label"] == 0


def test_frozen_risk_config_sizes_at_actual_fill_without_overriding_source_qty():
    bars = pd.DataFrame(
        {"open": [101.0], "high": [101.5], "low": [99.0], "close": [100.5], "volume": [100_000]},
        index=pd.to_datetime(["2026-01-02"]),
    )
    sig = signal()
    sig["regime"] = "risk_on"
    record = {
        "signal": sig,
        "status": "pending_entry",
        "qty": 0,
        "economics": settings().risk.model_dump(mode="json"),
    }
    forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "triggered_pending"
    assert record["qty"] == 0  # Source watchlist may have a different portfolio allocation.
    assert record["hypothetical_qty"] == 41  # floor(250 / (101.0505 - 95)).
    assert record["hypothetical_qty"] * (record["fill_price"] - 95) <= 250


def test_resolving_uses_indicator_history_before_arming(monkeypatch):
    bars = candle_frame(periods=60)
    armed = bars.index[-2].date().isoformat()
    sig = signal(armed)
    sig["trigger"] = 99.0
    seen = {}

    def simulate(sig, featured, entry_date, fill, qty, costs, **kwargs):
        seen["ema10"] = float(featured.loc[pd.Timestamp(entry_date), "ema10"])
        seen["first"] = featured.index[0]
        return {"status": "triggered_pending"}

    monkeypatch.setattr(forward, "simulate_outcome", simulate)
    record = {"signal": sig, "status": "pending_entry", "qty": 100}
    forward.resolve_record(record, bars, 0.0005)
    expected = forward.daily_features(bars).ema10.iloc[-1]
    assert seen["first"] == bars.index[0]
    assert seen["ema10"] == pytest.approx(expected)
    assert seen["ema10"] < bars.close.iloc[-1]


def test_prediction_and_entry_snapshots_are_immutable():
    bars = pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [100_000]},
        index=pd.to_datetime(["2026-01-02"]),
    )
    record = {
        "signal": signal(),
        "status": "pending_entry",
        "qty": 100,
        "contract_version": forward.CONTRACT_VERSION,
        "probabilities": {"a": 0.8},
        "features_sha256": "immutable",
    }
    record["prediction_sha256"] = forward._prediction_hash(record)
    frozen = record["prediction_sha256"]
    forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "triggered_pending"
    assert record["prediction_sha256"] == forward._prediction_hash(record) == frozen
    frozen_entry = record["entry_sha256"]
    forward.resolve_record(record, bars, 0.0005)
    assert record["entry_sha256"] == forward._entry_hash(record) == frozen_entry
    mutated = deepcopy(record)
    mutated["probabilities"]["a"] = 0.9
    with pytest.raises(ValueError, match="prediction payload"):
        forward.resolve_record(mutated, bars, 0.0005)
    mutated = deepcopy(record)
    mutated["fill_price"] = 99.9
    with pytest.raises(ValueError, match="entry payload"):
        forward.resolve_record(mutated, bars, 0.0005)


def test_legacy_migration_preserves_predictions_and_excludes_prior_results():
    legacy = {
        "signal_id": "old",
        "setup": "trend_pullback",
        "status": "resolved",
        "selected_for_research": True,
        "label": 1,
        "gross_r": 2,
        "probabilities": {"old_model": 0.9},
        "scored_at": "2026-01-01T18:00:00+05:30",
    }
    original = deepcopy(legacy)
    state = {"version": 1, "records": [legacy]}
    forward._migrate(state)
    assert all(legacy[key] == value for key, value in original.items())
    assert legacy["evidence_class"] == "legacy_unverified"
    assert legacy["legacy_payload_sha256"] == forward._feature_hash(original)
    assert not forward.resolve_record(legacy, pd.DataFrame(), 0.0005)
    summary = forward.summarize(state, None)
    assert summary["legacy_unverified"] == 1
    assert summary["all_scored"]["resolved"] == 0
    assert summary["selected_calls"] == 0


def test_only_closed_daily_bars_are_available_to_collection():
    bars = candle_frame("2026-09-16", periods=3)
    before_close = datetime(2026, 9, 17, 15, 29, tzinfo=forward.IST)
    at_close = datetime(2026, 9, 17, 15, 30, tzinfo=forward.IST)
    assert list(forward._closed_bars(bars, before_close).index.date) == [date(2026, 9, 16)]
    assert list(forward._closed_bars(bars, at_close).index.date) == [
        date(2026, 9, 16),
        date(2026, 9, 17),
    ]


@pytest.mark.parametrize(
    "scored_at, eligible",
    [
        ("2026-01-01T15:29:59+05:30", False),
        ("2026-01-01T15:30:00+05:30", True),
        ("2026-01-02T09:14:59+05:30", True),
        ("2026-01-02T09:15:00+05:30", False),
        ("2026-01-05T09:00:00+05:30", False),
    ],
)
def test_prospective_deadline_is_strict(tmp_path, scored_at, eligible):
    path = tmp_path / "source.json"
    path.write_text("{}", encoding="utf-8")
    report = {"id": "frozen"}
    activation = {"artifact_sha256": {"a": "sha"}, "contract_sha256": {}, "economics": {}}
    record = forward._score(
        {"signal": signal()},
        {},
        path,
        {"a": DummyBundle()},
        candle_frame(),
        {"dist_ema20_atr": 0.1},
        report,
        activation,
        datetime.fromisoformat(scored_at),
        set(),
        None,
    )
    assert record["prospective_eligible"] is eligible
    assert record["score_deadline"] == "2026-01-02T09:15:00+05:30"
    assert record["prediction_sha256"] == forward._prediction_hash(record)


def test_unknown_calendar_uses_early_cutoff_and_known_holidays_are_honored():
    friday = date(2026, 1, 2)
    assert forward._score_deadline(friday, set()).date() == date(2026, 1, 3)
    assert forward._score_deadline(friday, {date(2026, 1, 3)}).date() == date(2026, 1, 4)


def test_single_instance_lock_is_released_after_failure(tmp_path):
    with forward._lock(tmp_path, "watcher"):
        with pytest.raises(RuntimeError, match="already owns the lock"):
            with forward._lock(tmp_path, "watcher"):
                pytest.fail("Second collector must never start")
    with forward._lock(tmp_path, "watcher"):
        pass
