import json

import numpy as np
import pandas as pd
from tradedesk_lab import forward


class DummyBundle:
    features = ["dist_ema20_atr"]


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
        lambda output: (report, {"a": DummyBundle(), "b": DummyBundle()}, {"a": "1", "b": "2"}),
    )
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
    assert forward.resolve_record(record, bars, 0.0005)
    assert record["status"] == "resolved"
    assert record["outcome"] == "target" and record["label"] == 1
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
