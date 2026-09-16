import json

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.decision import Kind, from_entry, from_position
from tradedesk_lab.metrics import evaluate, select_threshold
from tradedesk_lab.models import agreement, fit


def test_agreement_requires_multiple_valid_families():
    a = agreement({"a": np.array([0.8, 0.4]), "b": np.array([0.8, 0.7])})
    assert a["spread"][0] == 0 and a["agree"].tolist() == [True, False]
    with pytest.raises(ValueError):
        agreement({"a": np.array([0.8])})
    with pytest.raises(ValueError):
        agreement({"a": np.array([np.nan]), "b": np.array([0.1])})


def test_temporal_calibration_and_predict_are_repeatable():
    rng = np.random.default_rng(17)
    dates = pd.bdate_range("2019-01-01", periods=400)
    x = rng.normal(size=350)
    df = pd.DataFrame(
        {
            "armed_on": dates[:350],
            "label_end_date": dates[5:355],
            "x": x,
            "label": (x > 0).astype(int),
        }
    )
    bundle = fit("logistic", {"C": 1.0}, df, ["x"], dates)
    p = bundle.predict(df)
    assert p[x > 1].mean() > p[x < -1].mean()
    assert np.array_equal(p, bundle.predict(df))


def test_no_positive_net_edge_means_abstain_and_no_fake_zero_hit_rate():
    df = pd.DataFrame({"label": [1] * 200, "net_r": [-1.0] * 200})
    p = np.ones(200) * 0.99
    threshold, _ = select_threshold(df, p)
    assert threshold > 1
    metrics = evaluate(df, p, p >= threshold)
    assert metrics["n"] == 0 and metrics["precision"] is None


def entry():
    return {
        "signal": {
            "id": "s1",
            "symbol": "SBIN",
            "setup": "base_breakout",
            "trigger": 100.0,
            "stop": 95.0,
            "t1": 110.0,
        },
        "qty": 10,
        "alertable": True,
        "rejected_for": [],
    }


def test_watchlist_is_not_a_confirmed_trade_and_reasons_survive():
    e = entry()
    assert from_entry(e, "today").kind == Kind.WATCHLIST
    assert from_entry(e, "today", confirmed=True).kind == Kind.WATCHLIST
    assert from_entry(e, "today", confirmed=True, health_ok=True, risk_ok=True).kind == Kind.TRADE
    e["rejected_for"] = ["net R:R too low", "weekly risk limit"]
    d = from_entry(e, "today", confirmed=True, health_ok=True, risk_ok=True)
    assert d.kind == Kind.NO_TRADE and list(d.reasons_against) == e["rejected_for"]
    encoded = json.loads(json.dumps(d.to_json()))
    assert [encoded[k] for k in ("entry", "stop", "target", "position_size")] == [100, 95, 110, 10]


def test_position_warnings_cannot_invent_risk_reduction():
    pos = {"signal": entry()["signal"], "entry_price": 101.0, "stop": 96.0, "qty_open": 7}
    d = from_position(pos, "today", warnings=("near stop", "model drift"))
    assert d.kind == Kind.HOLD
    assert from_position(pos, "today", exit_reasons=("stop hit",)).kind == Kind.EXIT_INVALIDATE
    assert (
        from_position(pos, "today", reduce_reasons=("existing partial exit",)).kind
        == Kind.REDUCE_RISK
    )


def test_optional_catboost_calibration_and_roundtrip(monkeypatch, tmp_path):
    import joblib
    from tradedesk_lab.artifacts import OUTPUT

    monkeypatch.syspath_prepend(str(OUTPUT / "deps"))
    pytest.importorskip("catboost")
    dates = pd.bdate_range("2020-01-01", periods=400)
    x = np.random.default_rng(14).normal(size=350)
    df = pd.DataFrame(
        {"armed_on": dates[:350], "label_end_date": dates[5:355], "x": x, "label": x > 0}
    )
    df.loc[::20, "x"] = np.nan
    model = fit("catboost", {"depth": 3, "iterations": 20}, df, ["x"], dates)
    predictions = model.predict(df)
    assert np.isfinite(predictions).all()
    assert predictions[x > 1].mean() > predictions[x < -1].mean()
    path = tmp_path / "bundle.joblib"
    joblib.dump(model, path)
    assert np.array_equal(predictions, joblib.load(path).predict(df))


def test_production_shadow_gates_remain_disabled():
    import yaml
    from tradedesk_lab.artifacts import ROOT

    config = yaml.safe_load((ROOT / "config/ml.yaml").read_text(encoding="utf-8"))
    assert config["enabled"] is False
    assert config["shadow"] is True
