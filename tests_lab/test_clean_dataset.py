import json

import duckdb
import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import clean_dataset
from tradedesk_lab.artifacts import ROOT

from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.signals import Signal


def candles():
    dates = pd.bdate_range("2023-01-02", periods=300)
    return pd.DataFrame(
        {"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 1_000_000},
        index=dates,
    )


def sig():
    return Signal(
        id="valid",
        scrip_code="NSE_1",
        symbol="TEST",
        setup="nr7_breakout",
        armed_on=candles().index[260].date(),
        trigger=100.05,
        stop=95,
        t1=110,
        t2=115,
        atr=4,
    )


def test_features_ignore_future_candles_context_and_saved_fill_geometry():
    frame = candles()
    frame.close = 100 + np.sin(np.arange(len(frame)) / 7)
    signal = sig()
    first = clean_dataset.point_in_time_features(signal, daily_features(frame), frame, frame)
    changed = frame.copy()
    changed.loc[changed.index > pd.Timestamp(signal.armed_on)] *= 10
    second = clean_dataset.point_in_time_features(
        signal.model_copy(update={"trigger": 101.0, "stop": 90, "t1": 115}),
        daily_features(changed),
        changed,
        changed,
    )
    assert first == second
    assert set(first) == set(clean_dataset.FEATURES)
    assert "sessions_to_results" not in first and "stop_atr" not in first


def test_stale_market_context_fails_closed():
    frame = candles()
    with pytest.raises(ValueError, match="stale_benchmark"):
        clean_dataset.point_in_time_features(sig(), daily_features(frame), frame.iloc[:260], frame)
    with pytest.raises(ValueError, match="stale_vix"):
        clean_dataset.point_in_time_features(sig(), daily_features(frame), frame, frame.iloc[:260])


def test_bar_integrity():
    frame = candles()
    assert clean_dataset.valid_bars(frame)
    frame.iloc[5, frame.columns.get_loc("high")] = 99
    assert not clean_dataset.valid_bars(frame)


def test_rebuild_rejects_bad_geometry_and_preserves_old_artifacts(tmp_path, monkeypatch):
    settings = load_config(ROOT)
    monkeypatch.setattr(clean_dataset, "load_config", lambda _: settings)
    config = tmp_path / "config"
    config.mkdir()
    for name in ("risk.yaml", "universe.yaml"):
        (config / name).write_bytes((ROOT / "config" / name).read_bytes())
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    frame = candles()
    row = {
        "signal_id": "valid",
        "scrip_code": "NSE_1",
        "setup": "nr7_breakout",
        "armed_on": frame.index[260],
        "entry_date": frame.index[261],
        "entry": 100.05,
        "stop": 95,
        "t1": 110,
        "t2": 115,
        "atr": 4,
    }
    pd.DataFrame([row, row | {"signal_id": "bad", "t1": 99}]).to_csv(
        reports / "barrier_signals.csv",
        index=False,
    )
    with duckdb.connect(str(tmp_path / "data/tradedesk.duckdb")) as con:
        con.execute(
            "CREATE TABLE instruments(scrip_code VARCHAR, trading_symbol VARCHAR, "
            "exch VARCHAR, kind VARCHAR)"
        )
        con.execute(
            "INSERT INTO instruments VALUES ('BENCH','NIFTY 50','NSE','index'), "
            "('VIX','INDIA VIX','NSE','index')"
        )
        rows = frame.copy()
        rows["ts"] = frame.index.tz_localize("Asia/Kolkata").as_unit("s").asi8
        rows["interval"] = "1day"
        rows["scrip_code"] = "NSE_1"
        con.register("rows", rows)
        con.execute("CREATE TABLE candles AS SELECT * FROM rows")
        for code in ("BENCH", "VIX"):
            rows["scrip_code"] = code
            con.register("rows", rows)
            con.execute("INSERT INTO candles SELECT * FROM rows")
    output = tmp_path / "lab"
    output.mkdir()
    (output / "dataset.joblib").write_bytes(b"old frozen cache")
    (output / "latest.json").write_text('{"id":"old"}')
    result = clean_dataset.prepare_clean(tmp_path, output)
    assert len(result.frame) == 1 and result.frame.iloc[0].label == 0
    assert result.manifest["label_version"] == "net-target-v2"
    assert result.manifest["excluded"] == {"target_at_or_below_fill": 1}
    assert result.frame.iloc[0].net_r < result.frame.iloc[0].gross_r
    assert (output / "dataset.joblib").read_bytes() == b"old frozen cache"
    assert json.loads((output / "latest.json").read_text()) == {"id": "old"}
    loaded = clean_dataset.load_prepared(tmp_path, output)
    pd.testing.assert_frame_equal(result.frame, loaded.frame)
    (config / "risk.yaml").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        clean_dataset.load_prepared(tmp_path, output)
