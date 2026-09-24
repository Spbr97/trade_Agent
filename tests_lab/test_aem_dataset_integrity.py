from types import SimpleNamespace

import pandas as pd
from tradedesk_lab import aem_dataset


def _bars(index):
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100},
        index=index,
    )


def test_corrupt_future_session_cannot_erase_valid_earlier_events(monkeypatch):
    days = pd.bdate_range("2026-08-01", periods=5, tz="Asia/Kolkata")
    good = _bars(pd.date_range(f"{days[-2].date()} 09:15", periods=375, freq="min", tz=days.tz))
    bad = _bars(pd.date_range(f"{days[-1].date()} 09:15", periods=375, freq="min", tz=days.tz))
    bad.iloc[100, bad.columns.get_loc("high")] = float("nan")
    monkeypatch.setattr(aem_dataset, "daily_features", lambda frame: frame)
    monkeypatch.setattr(
        aem_dataset,
        "detect_daily_candidate",
        lambda code, symbol, frame, on, contract: SimpleNamespace(
            identifier=f"{code}:{on}", symbol=symbol, armed_on=on, features={}
        ),
    )
    monkeypatch.setattr(
        aem_dataset,
        "evaluate_trigger",
        lambda candidate, session, at, contract: SimpleNamespace(
            decision="WATCH",
            features={"checks": {"test": False}},
            reasons=["test_no_trigger"],
            available_at=at,
        ),
    )
    kwargs = dict(
        risk=None, costs=None, trading_dates=list(days.date), evaluation_dates=list(days[-2:].date)
    )
    before, _ = aem_dataset.build_events(
        {"NSE_NEW": _bars(days)}, {"NSE_NEW": good}, {"NSE_NEW": "UNSEEN"}, **kwargs
    )
    after, audit = aem_dataset.build_events(
        {"NSE_NEW": _bars(days)},
        {"NSE_NEW": pd.concat([good, bad])},
        {"NSE_NEW": "UNSEEN"},
        **kwargs,
    )
    assert len(before) == 1
    pd.testing.assert_frame_equal(before, after)
    assert audit["invalid_ohlcv_sessions"] == 1 and audit["invalid_ohlcv_bars"] == 1
