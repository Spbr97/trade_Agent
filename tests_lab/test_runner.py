"""Exercise orchestration with poisoned final features and deterministic fake models."""

import numpy as np
import pandas as pd
from tradedesk_lab import runner
from tradedesk_lab.dataset import Dataset
from tradedesk_lab.registry import Registry


class ConstantBundle:
    def predict(self, df):
        return np.full(len(df), 0.3)


def test_full_orchestration_keeps_holdout_out_of_all_training(monkeypatch, tmp_path):
    dates = pd.bdate_range("2020-01-01", periods=420)
    n = 400
    split = dates[320]
    df = pd.DataFrame(
        {
            "signal_id": [str(i) for i in range(n)],
            "scrip_code": "example",
            "armed_on": dates[:n],
            "entry_date": dates[1 : n + 1],
            "label_end_date": dates[6 : n + 6],
            "setup": ["a", "b"] * (n // 2),
            "label": np.arange(n) % 2,
            "net_r": -1.0,
            "regime": "risk_on",
            "poison": np.where(dates[:n] >= split, 999, 0),
        }
    )
    dataset = Dataset(df, ["poison"], dates, {}, {"market": "test"})
    fitted = []

    def fit(family, hp, train, features, calendar):
        assert (train.poison == 0).all()
        assert train.label_end_date.max() < split
        fitted.append(family)
        return ConstantBundle()

    def replay(ds, frame, mask):
        assert len(frame) == len(mask)
        equity = pd.Series(0.0, index=pd.DatetimeIndex(frame.armed_on.unique()))
        return {"sharpe": None, "trades": int(mask.sum())}, equity

    monkeypatch.setattr(runner, "OUTPUT", tmp_path)
    monkeypatch.setattr(runner, "fit", fit)
    monkeypatch.setattr(runner, "replay", replay)
    monkeypatch.setattr(runner, "grids", lambda: ({"a": [{"x": 1}], "b": [{"x": 2}]}, {}))
    monkeypatch.setattr(runner, "git", lambda *args: "test")
    monkeypatch.setattr(runner, "verify_base", lambda: {"unchanged": True})
    report = runner.run(dataset)
    assert fitted and report["cpcv"]["n_paths"] == 5
    assert report["champion"] is None
    stages = report["historical_holdout"]
    assert stages["existing_candidates"]["n"] == 80
    assert stages["ensemble_agreement"]["n"] == 0
    assert report["eligibility"]["live_enabled"] is False
    with Registry(tmp_path / "registry.sqlite", readonly=True) as registry:
        saved = registry.run(report["id"])
        assert saved["status"] == "completed"
        assert len(saved["candidates"]) == 10
        assert registry.con.execute("SELECT COUNT(*) FROM holdouts").fetchone()[0] == 1
