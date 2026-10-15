"""intraday_mover_study.py: features use only bars before the decision, references only
prior sessions, entry is the first bar that starts after the decision, and the live path
computes exactly what the historical path computes."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import intraday_mover_study as ims
from tradedesk_lab import mover_study as daily

from tradedesk.broker.indstocks.models import IST, Candle, Interval

SESSIONS = list(pd.bdate_range("2026-08-03", periods=30).date)


def _bars(codes=("NSE_1", "NSE_2", "NSE_3"), sessions=SESSIONS, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for code in codes:
        price = 100.0
        for s in sessions:
            for i in range(25):
                start = ims.bar_start(s, i)
                o = price
                c = o * (1 + rng.normal(0, 0.003))
                rows.append(
                    {"code": code, "ts": int(start.timestamp()), "open": o,
                     "high": max(o, c) * 1.001, "low": min(o, c) * 0.999, "close": c,
                     "volume": float(400_000 + 1000 * i)}
                )  # fmt: skip
                price = c
    return pd.DataFrame(rows)


def test_bars_are_indexed_by_the_ist_clock_and_entry_skips_the_running_bar() -> None:
    bars = _bars(codes=("NSE_1",), sessions=SESSIONS[:1])
    agg = ims.session_aggregates(bars).iloc[0]
    day = bars.reset_index(drop=True)
    assert agg["date"] == SESSIONS[0] and agg["n_bars"] == 25
    assert agg["open0"] == pytest.approx(day.loc[0, "open"])  # 09:15
    assert agg["close_last"] == pytest.approx(day.loc[24, "close"])  # 15:15 bar close
    assert agg["c_4"] == pytest.approx(day.loc[3, "close"])  # the 10:00 bar ends at 10:15
    assert agg["hi_4"] == pytest.approx(day.loc[0:3, "high"].max())  # 10:15 bar excluded
    assert agg["entry_4"] == pytest.approx(day.loc[5, "open"])  # the 10:30 bar


def test_features_ignore_every_bar_from_the_decision_onward() -> None:
    base = _bars()
    changed = base.copy()
    when = pd.to_datetime(changed["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    late = (when.dt.date == SESSIONS[-1]) & (when.dt.strftime("%H:%M") >= "10:15")
    changed.loc[late, ["high", "close"]] *= 1.5  # later closes move; the 10:30 entry open does not
    a = ims.add_features(ims.session_aggregates(base))["first_hour"]
    b = ims.add_features(ims.session_aggregates(changed))["first_hour"]
    last_a = a[a["date"] == SESSIONS[-1]].set_index("code")
    last_b = b[b["date"] == SESSIONS[-1]].set_index("code")
    pd.testing.assert_frame_equal(last_a[list(ims.FEATURES)], last_b[list(ims.FEATURES)])
    assert not np.allclose(last_a["rest_of_day"], last_b["rest_of_day"])  # targets do move


def test_references_come_from_prior_sessions_only() -> None:
    frames = ims.add_features(ims.session_aggregates(_bars()))
    f = frames["first_hour"]
    one = f[f["code"] == "NSE_1"].set_index("date")
    agg = ims.session_aggregates(_bars()).query("code == 'NSE_1'").set_index("date")
    target = SESSIONS[-1]
    prior = agg.loc[[d for d in SESSIONS if d < target][-ims.REFERENCE_SESSIONS :], "v_4"]
    assert one.loc[target, "vol_rel"] == pytest.approx(agg.loc[target, "v_4"] / prior.mean())


def _candles(bars: pd.DataFrame, session: date, upto: int) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    for row in bars.itertuples():
        ts = datetime.fromtimestamp(row.ts, tz=IST)
        if ts.date() != session or ts >= ims.bar_start(session, upto):
            continue
        out.setdefault(row.code, []).append(
            Candle(scrip_code=row.code, interval=Interval.M15, ts=ts, open=row.open,
                   high=row.high, low=row.low, close=row.close, volume=int(row.volume))
        )  # fmt: skip
    return out


def test_live_features_equal_the_historical_definition() -> None:
    bars = _bars()
    today = SESSIONS[-1]
    hist = ims.add_features(ims.session_aggregates(bars))["first_hour"]
    hist = hist[hist["date"] == today].set_index("code")
    refs = ims.reference_values("nse", today, bars=bars[
        pd.to_datetime(bars["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date < today
    ])  # fmt: skip
    live = ims.live_features(_candles(bars, today, upto=5), refs, today, 4).set_index("code")
    for feature in ims.FEATURES:
        assert live[feature].to_numpy() == pytest.approx(
            hist.loc[live.index, feature].to_numpy(), rel=1e-9
        ), feature


def test_live_registration_happens_only_inside_the_decision_window(tmp_path) -> None:
    bars = _bars()
    today = SESSIONS[-1]
    prior = bars[
        pd.to_datetime(bars["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date < today
    ]  # noqa: E501
    refs = ims.reference_values("nse", today, bars=prior)
    daily.append_chain(
        tmp_path / "candidates.jsonl",
        {"event": "candidate_activated", "name": "first_hour:r_open_top10",
         "target": "rest_of_day", "checkpoint": "first_hour", "activated_at": "2026-09-01",
         "clauses": [{"feature": "r_open", "side": "top", "quantile": 0.5}]},
    )  # fmt: skip
    candles = _candles(bars, today, upto=5)

    def at(hhmm: str) -> datetime:
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime(today.year, today.month, today.day, h, m, tzinfo=IST)

    assert (
        ims.register_live("nse", candles, now=at("10:14"), root=tmp_path, refs=refs)["registered"]
        == 0
    )  # noqa: E501
    assert (
        ims.register_live("nse", candles, now=at("10:16"), root=tmp_path, refs=refs)["registered"]
        == 1
    )  # noqa: E501
    assert (
        ims.register_live("nse", candles, now=at("10:20"), root=tmp_path, refs=refs)["registered"]
        == 0
    )  # noqa: E501
    rows, intact = daily.read_chain(tmp_path / "forward_picks.jsonl")
    assert intact and rows[0]["entry_bar"] == "10:30" and rows[0]["codes"]


def test_forward_scores_use_the_same_entry_bar_null_and_drop_late_rows(tmp_path) -> None:
    frames = ims.add_features(ims.session_aggregates(_bars()))
    today = SESSIONS[-1]
    day = frames["first_hour"][frames["first_hour"]["date"] == today]
    pick = day["code"].iloc[0]
    for registered in ("2026-09-11T10:16:00+05:30", "2026-09-11T10:31:00+05:30"):
        daily.append_chain(
            tmp_path / "forward_picks.jsonl",
            {"event": "picks_registered", "candidate": "c", "target": "rest_of_day",
             "checkpoint": "first_hour", "decision_session": today.isoformat(),
             "registered_at": registered, "deadline": "2026-09-11T10:30:00+05:30",
             "codes": [pick]},
        )  # fmt: skip
    scores = ims.forward_scores(tmp_path, frames, {"rest_of_day": 0.0, "to_next_close": 0.0})
    assert len(scores) == 1 and scores[0]["forward_days"] == 1  # the 10:31 row is ignored
    expected = float(day.set_index("code").loc[pick, "rest_of_day"] - day["rest_of_day"].mean())
    assert scores[0]["mean_excess"] == pytest.approx(expected)


def test_opening_checkpoint_registers_on_the_09_31_run_and_enters_at_09_45(tmp_path) -> None:
    bars = _bars()
    today = SESSIONS[-1]
    prior = bars[
        pd.to_datetime(bars["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date < today
    ]
    refs = ims.reference_values("nse", today, bars=prior)
    daily.append_chain(
        tmp_path / "candidates.jsonl",
        {"event": "candidate_activated", "name": "opening_bar:r_open_top10",
         "target": "short_rest_of_day", "checkpoint": "opening_bar",
         "activated_at": "2026-09-01",
         "clauses": [{"feature": "r_open", "side": "top", "quantile": 0.5}]},
    )  # fmt: skip
    candles = _candles(bars, today, upto=2)
    at = datetime(today.year, today.month, today.day, 9, 31, tzinfo=IST)
    assert ims.register_live("nse", candles, now=at, root=tmp_path, refs=refs)["registered"] == 1
    rows, _ = daily.read_chain(tmp_path / "forward_picks.jsonl")
    assert rows[0]["entry_bar"] == "09:45" and rows[0]["target"] == "short_rest_of_day"


def test_short_target_profits_when_the_stock_falls_after_entry() -> None:
    frames = ims.add_features(ims.session_aggregates(_bars()))
    f = frames["opening_bar"].dropna(subset=["rest_of_day", "short_rest_of_day"])
    assert np.allclose(
        (1 + f["rest_of_day"]) * (1 + f["short_rest_of_day"]), 1.0
    )  # close/entry and entry/close are reciprocals
    falling = f[f["rest_of_day"] < 0]
    assert (falling["short_rest_of_day"] > 0).all()


def test_one_bonferroni_bar_covers_every_checkpoint(monkeypatch) -> None:
    calls = []

    def fake_train(frame, costs, **_kw):
        n = int(frame["n"].iloc[0])
        return {
            "status": "trained", "passed": [{}] * n, "validation": None, "sessions": 1,
            "train": ["a", "b"], "validation_from": "b", "validation_to": "c", "tested": 10,
        }  # fmt: skip

    def fake_validate(trained, costs, bar):
        calls.append(bar)
        return {"status": "searched", "survivors": [], "near_misses": [], "tested": 10}

    monkeypatch.setattr(daily, "train_phase", fake_train)
    monkeypatch.setattr(daily, "validate_phase", fake_validate)
    frames = {"opening_bar": pd.DataFrame({"n": [3]}), "first_hour": pd.DataFrame({"n": [5]})}
    out = ims.search(frames, {t: 0.0 for t in ims.TARGETS})
    assert out["family_passed_train"] == 8
    assert calls == [daily.family_bar(8)] * 2  # the same, stricter bar for both checkpoints
    assert daily.family_bar(8) > daily.family_bar(5)


def _crypto_bars(days: int = 26, codes=("CDX_A", "CDX_B", "CDX_C"), seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-09-01", tz="UTC")
    rows = []
    for code in codes:
        price = 1000.0
        for i in range(days * 96):
            ts = start + pd.Timedelta(minutes=15 * i)
            o = price
            c = o * (1 + rng.normal(0, 0.004))
            rows.append(
                {"code": code, "ts": int(ts.timestamp()), "open": o, "high": max(o, c) * 1.001,
                 "low": min(o, c) * 0.999, "close": c, "volume": 37.25 + (i % 96) * 0.5}
            )  # fmt: skip
            price = c
    return pd.DataFrame(rows)


def test_crypto_sessions_are_utc_days_starting_05_30_ist_and_never_short() -> None:
    first = ims.bar_start(date(2026, 9, 2), 0, "crypto")
    assert first.astimezone(IST).strftime("%H:%M") == "05:30"
    assert ims.bar_start(date(2026, 9, 2), 5, "crypto").astimezone(IST).strftime("%H:%M") == "06:45"
    agg = ims.session_aggregates(_crypto_bars(days=2, codes=("CDX_A",)), "crypto")
    assert list(agg["n_bars"]) == [96, 96]
    assert "short_rest_of_day" not in ims.targets_for("crypto")
    assert "short_rest_of_day" in ims.targets_for("nse")


def test_crypto_live_features_from_exact_volume_bars_equal_history() -> None:
    from tradedesk_lab.intraday_history import Bar

    bars = _crypto_bars()
    stamps = pd.to_datetime(bars["ts"], unit="s", utc=True)
    today = stamps.dt.date.max()
    hist = ims.add_features(ims.session_aggregates(bars, "crypto"), "crypto")["first_hour"]
    hist = hist[hist["date"] == today].set_index("code")
    refs = ims.reference_values("crypto", today, bars=bars[stamps.dt.date < today])
    live_bars: dict[str, list[Bar]] = {}
    for row in bars[stamps.dt.date == today].itertuples():
        ts = datetime.fromtimestamp(row.ts, tz=IST)
        if ts < ims.bar_start(today, 5, "crypto"):
            live_bars.setdefault(row.code, []).append(
                Bar(ts, row.open, row.high, row.low, row.close, row.volume)
            )
    live = ims.live_features(live_bars, refs, today, 4, "crypto").set_index("code")
    assert len(live) == 3
    for feature in ims.FEATURES:
        assert live[feature].to_numpy() == pytest.approx(
            hist.loc[live.index, feature].to_numpy(), rel=1e-9
        ), feature


def test_aggregating_inside_the_history_database_matches_the_in_memory_path(
    tmp_path, monkeypatch
) -> None:
    from tradedesk_lab import intraday_history
    from tradedesk_lab.intraday_history import Bar

    monkeypatch.setattr(intraday_history, "ROOT", tmp_path)
    bars = _bars(sessions=SESSIONS[:5])
    by_code: dict[str, list[Bar]] = {}
    for row in bars.itertuples():
        by_code.setdefault(row.code, []).append(
            Bar(datetime.fromtimestamp(row.ts, tz=IST), row.open, row.high, row.low, row.close,
                row.volume)
        )  # fmt: skip
    intraday_history.store("nse", by_code)
    start = datetime(2026, 8, 1, tzinfo=IST)
    end = datetime(2026, 9, 1, tzinfo=IST)
    stored = ims.stored_session_aggregates("nse", start, end).sort_values(["code", "date"])
    memory = ims.session_aggregates(bars).sort_values(["code", "date"])
    pd.testing.assert_frame_equal(
        stored.reset_index(drop=True), memory.reset_index(drop=True), check_dtype=False
    )


def test_references_are_computed_once_per_session_and_reused(tmp_path, monkeypatch) -> None:
    calls = []
    frame = pd.DataFrame([{"code": "NSE_1", "prev_close": 100.0, "ret5": 0.01, "rng_ref": 0.02,
                           "notional_ref": 3e7, "vol_ref_4": 1e5}])  # fmt: skip

    def compute(market, as_of):
        calls.append(as_of)
        return frame

    monkeypatch.setattr(ims, "reference_values", compute)
    first = ims.cached_reference_values("nse", SESSIONS[-1], tmp_path)
    again = ims.cached_reference_values("nse", SESSIONS[-1], tmp_path)
    assert calls == [SESSIONS[-1]]
    pd.testing.assert_frame_equal(first, again, check_dtype=False)
    ims.cached_reference_values("nse", SESSIONS[-2], tmp_path)
    assert calls == [SESSIONS[-1], SESSIONS[-2]]  # a new session recomputes


def test_consensus_registers_the_stocks_most_short_rules_agree_on(tmp_path) -> None:
    bars = _bars(codes=tuple(f"NSE_{i}" for i in range(1, 11)))
    today = SESSIONS[-1]
    prior = bars[
        pd.to_datetime(bars["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date < today
    ]
    refs = ims.reference_values("nse", today, bars=prior)
    for name, quantile in (("a", 0.5), ("b", 0.8), ("c", 0.9)):
        daily.append_chain(
            tmp_path / "candidates.jsonl",
            {"event": "candidate_activated", "name": f"opening_bar:{name}",
             "target": "short_rest_of_day", "checkpoint": "opening_bar",
             "activated_at": "2026-09-01",
             "clauses": [{"feature": "r_open", "side": "top", "quantile": quantile}]},
        )  # fmt: skip
    daily.append_chain(
        tmp_path / "candidates.jsonl",
        {"event": "candidate_activated", "name": "opening_bar:long",
         "target": "rest_of_day", "checkpoint": "opening_bar", "activated_at": "2026-09-01",
         "clauses": [{"feature": "r_open", "side": "top", "quantile": 0.1}]},
    )  # fmt: skip
    at = datetime(today.year, today.month, today.day, 9, 31, tzinfo=IST)
    candles = _candles(bars, today, upto=2)
    result = ims.register_live("nse", candles, now=at, root=tmp_path, refs=refs)
    assert result["registered"] == 4 and result["consensus_registered"] == 1
    rows, intact = daily.read_chain(tmp_path / "forward_picks.jsonl")
    consensus = [r for r in rows if r["candidate"].startswith("consensus_top")]
    assert intact and len(consensus) == 1
    row = consensus[0]
    assert row["target"] == "short_rest_of_day"
    assert row["rules_voting"] == 3  # the long rule never votes
    assert 1 <= len(row["codes"]) <= ims.CONSENSUS_N
    votes = [row["votes"][c] for c in row["codes"]]
    assert votes == sorted(votes, reverse=True)
    again = ims.register_live("nse", candles, now=at, root=tmp_path, refs=refs)
    assert again["consensus_registered"] == 0  # one registration per session and checkpoint


def test_pick_record_counts_winners_after_costs_and_against_the_average(tmp_path) -> None:
    day = SESSIONS[-1]
    frame = pd.DataFrame(
        {
            "date": [day] * 4,
            "code": ["A", "B", "C", "D"],
            "short_rest_of_day": [0.02, 0.001, -0.01, 0.0],  # mean 0.0027
        }
    )
    base = {
        "event": "picks_registered", "target": "short_rest_of_day", "checkpoint": "opening_bar",
        "decision_session": day.isoformat(), "registered_at": "2026-10-15T09:31:00+05:30",
        "deadline": "2026-10-15T09:45:00+05:30",
    }  # fmt: skip
    chain = tmp_path / "forward_picks.jsonl"
    daily.append_chain(
        chain, {**base, "candidate": "consensus_top3:opening_bar", "codes": ["A", "B", "C"]}
    )
    daily.append_chain(chain,
                       {**base, "candidate": "opening_bar:x", "codes": ["A", "C"]})
    daily.append_chain(chain,
                       {**base, "candidate": "opening_bar:y", "codes": ["A", "D"]})
    record = ims.pick_record(
        tmp_path, {"opening_bar": frame}, {"short_rest_of_day": 0.0016}
    )
    consensus = record["consensus"]["all"]
    # A wins and beats the average; B (0.001 gross) loses after costs; C loses
    assert (consensus["picks"], consensus["winners"], consensus["beat_market"]) == (3, 1, 1)
    rules = record["rules"]["all"]
    assert rules["picks"] == 3  # A is counted once even though two rules picked it
