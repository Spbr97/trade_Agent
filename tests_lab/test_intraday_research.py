"""Causal geometry, executable fills and capped intraday research replay."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.intraday_research import (
    BAR,
    ResearchSetupKind,
    ResearchSignal,
    ResolvedCandidate,
    _size_and_cost,
    _stop_loss,
    fill_candidate,
    matched_random,
    portfolio_replay,
    research_registry,
    resolve_barriers,
    summarize,
)

from tradedesk.broker.indstocks.models import Interval
from tradedesk.config.models import RiskConfig
from tradedesk.engine.intraday_engine import IntradaySnapshot, scan_bar
from tradedesk.markets.costs import EquityCostModel
from tradedesk.setups.intraday import INTRADAY_REGISTRY


def _frame(kind: ResearchSetupKind) -> pd.DataFrame:
    rows = [
        dict(
            open=100.4,
            high=101.0,
            low=100.0,
            close=100.6,
            volume=1000,
            atr14=1.0,
            vol_ratio20=1.5,
            session_bar=i,
        )
        for i in range(7)
    ]
    if kind == ResearchSetupKind.SUPPORT_REVERSAL:
        rows[-1].update(open=100.2, high=101.1, low=99.98, close=100.95)
    else:
        rows[-2].update(open=100.8, high=102.2, low=100.8, close=102.0)
        rows[-1].update(open=101.3, high=101.9, low=100.95, close=101.8)
    return pd.DataFrame(
        rows,
        index=pd.date_range(
            "2026-09-16 09:15",
            periods=len(rows),
            freq="5min",
            tz="Asia/Kolkata",
        ),
    )


def _scan(frame: pd.DataFrame, kind: ResearchSetupKind, at=None):
    snapshot = IntradaySnapshot(
        at=at if at is not None else frame.index[-1] + BAR,
        arming_interval=Interval.M5,
        features={"NSE_1": frame},
        symbols={"NSE_1": "TEST"},
    )
    return scan_bar(snapshot, [kind], {})


@pytest.mark.parametrize("kind", list(ResearchSetupKind))
def test_future_and_forming_bars_cannot_change_signal(kind):
    frame = _frame(kind)
    future = frame.iloc[-2:].copy()
    future.index = pd.date_range(frame.index[-1] + BAR, periods=2, freq="5min")
    future.loc[:, ["open", "high", "low", "close", "atr14", "vol_ratio20"]] = 0.001
    with research_registry():
        normal = _scan(frame, kind)
        poisoned = _scan(pd.concat([frame, future]), kind, frame.index[-1] + BAR)
        assert len(normal) == 1
        assert normal[0].model_dump() == poisoned[0].model_dump()
        assert normal[0].armed_at == frame.index[-1] + BAR
        assert normal[0].t1 == pytest.approx(
            normal[0].trigger + 2 * (normal[0].trigger - normal[0].stop),
        )
        assert _scan(frame, kind, frame.index[-1] + BAR - pd.Timedelta(seconds=1)) == []


def test_breakout_requires_a_distinct_later_retest():
    frame = _frame(ResearchSetupKind.BREAKOUT_RETEST)
    with research_registry():
        assert _scan(frame.iloc[:-1], ResearchSetupKind.BREAKOUT_RETEST) == []
        assert len(_scan(frame, ResearchSetupKind.BREAKOUT_RETEST)) == 1
        frame.loc[frame.index[-1], "low"] = 101.5
        assert _scan(frame, ResearchSetupKind.BREAKOUT_RETEST) == []


def test_registry_restored_after_error_and_nested_use_rejected():
    before = INTRADAY_REGISTRY.copy()
    with pytest.raises(ValueError, match="test"):
        with research_registry():
            with pytest.raises(RuntimeError, match="already active"):
                with research_registry():
                    pass
            raise ValueError("test")
    assert INTRADAY_REGISTRY == before


def _fill_fixture():
    frame = _frame(ResearchSetupKind.SUPPORT_REVERSAL)
    with research_registry():
        sig = _scan(frame, ResearchSetupKind.SUPPORT_REVERSAL)[0]
    # Huge confirmation-bar high is not a target hit: trade does not exist yet.
    later = pd.DataFrame(
        [
            dict(open=101.0, high=1000.0, low=100.5, close=101.2),
            dict(open=101.2, high=101.4, low=100.4, close=101.1),
            dict(open=101.1, high=101.3, low=100.5, close=101.2),
        ],
        index=pd.date_range(frame.index[-1] + BAR, periods=3, freq="5min"),
    )
    return pd.concat([frame, later]), sig


def test_fill_after_confirmation_never_counts_preentry_target():
    frame, sig = _fill_fixture()
    trade, why = fill_candidate(sig, frame, 6, 0, 9, 0.0005)
    assert why == "filled" and trade is not None
    assert trade.entry_bar == 8
    assert trade.entry_at == frame.index[8].isoformat()
    assert pd.Timestamp(trade.entry_at) > pd.Timestamp(trade.decision_at)
    assert trade.entry == pytest.approx(101.2 * 1.0005)
    assert not trade.target_hit
    assert trade.exit_reason == "session_close"


def test_no_execution_if_target_is_below_actual_fill_or_no_next_bar():
    frame, sig = _fill_fixture()
    frame.loc[frame.index[8], "open"] = sig.t1 + 1
    trade, why = fill_candidate(sig, frame, 6, 0, 9, 0)
    assert trade is None and why == "invalid_target_or_stop_at_fill"
    trade, why = fill_candidate(sig, frame.iloc[:8], 6, 0, 7, 0)
    assert trade is None and why == "no_next_session_bar"


def test_missing_entry_bar_is_not_filled_at_a_later_open():
    frame, sig = _fill_fixture()
    frame = frame.drop(frame.index[8])
    trade, why = fill_candidate(sig, frame, 6, 0, 8, 0)
    assert trade is None and why == "missing_entry_bar"


def test_stop_wins_target_tie_and_gaps_fill_at_open():
    arrays = tuple(np.array(x, float) for x in ([100, 97], [110, 98], [98, 96], [105, 97]))
    assert resolve_barriers(arrays, 0, 1, stop=99, target=104, slippage=0) == (0, 99, "stop")
    assert resolve_barriers(arrays, 1, 1, stop=99, target=104, slippage=0) == (
        1,
        97,
        "gap_stop",
    )


def _candidate(code="NSE_1", *, entry_at="10:00", exit_at="15:30", exit_price=108.0):
    sig = ResearchSignal(
        id=f"test:{code}",
        scrip_code=code,
        symbol=code,
        setup=ResearchSetupKind.SUPPORT_REVERSAL,
        interval=Interval.M5,
        armed_at=pd.Timestamp("2026-09-16 09:50", tz="Asia/Kolkata"),
        trigger=102,
        stop=100,
        t1=106,
        t2=106,
        atr=1,
    )
    # A better next-open entry gives sufficient net RR for the hard 2R net gate.
    return ResolvedCandidate(
        sig,
        "2026-09-16",
        sig.armed_at.isoformat(),
        f"2026-09-16T{entry_at}:00+05:30",
        f"2026-09-16T{exit_at}:00+05:30",
        1,
        0,
        3,
        101,
        exit_price,
        100,
        106,
        (exit_price - 101),
        exit_price >= 106,
        "target" if exit_price >= 106 else "stop",
    )


def test_sizing_includes_fees_stop_slippage_and_floor_whole_shares():
    risk = RiskConfig(trading_capital=Decimal("100000"))
    costs = EquityCostModel(risk.costs)
    trade = _candidate()
    qty, fees, _ = _size_and_cost(trade, 100000, risk, costs)
    assert qty > 0 and qty == int(qty)
    assert qty * trade.entry <= 25000
    assert fees > 0
    assert _stop_loss(trade, qty, costs) <= 250
    assert _stop_loss(trade, qty + 1, costs) > 250


def test_portfolio_daily_caps_and_future_outcome_not_available_at_entry():
    risk = RiskConfig(trading_capital=Decimal("100000"))
    costs = EquityCostModel(risk.costs)
    trades = [_candidate(f"NSE_{i}") for i in range(5)]
    sectors = {t.signal.scrip_code: t.signal.scrip_code for t in trades}
    result = portfolio_replay(trades, risk, costs, sectors, ["2026-09-16"])
    assert result["trades"] == 3
    assert result["rejections"] == {"max new entries today": 2}
    damaged = [replace(trades[0], exit=50, gross_r=-51, target_hit=False), *trades[1:]]
    alternate = portfolio_replay(damaged, risk, costs, sectors, ["2026-09-16"])
    assert [r["qty"] for r in result["rows"]] == [r["qty"] for r in alternate["rows"]]
    assert alternate["ending_capital"] < result["ending_capital"]


def test_portfolio_unknown_sector_is_capped_and_net_rr_gate_is_retained():
    risk = RiskConfig(trading_capital=Decimal("100000"))
    costs = EquityCostModel(risk.costs)
    trades = [_candidate(f"NSE_{i}") for i in range(4)]
    sectors = {t.signal.scrip_code: "UNKNOWN" for t in trades}
    result = portfolio_replay(trades, risk, costs, sectors, ["2026-09-16"])
    assert result["trades"] == 2
    assert result["rejections"] == {"sector cap (UNKNOWN)": 2}
    bad_rr = replace(trades[0], entry=103, target=106)
    result = portfolio_replay([bad_rr], risk, costs, sectors, ["2026-09-16"])
    assert result["trades"] == 0
    assert result["rejections"] == {"configured_min_net_rr": 1}


def test_session_close_profit_is_not_success_and_no_call_sessions_stay_visible():
    rows = [
        {
            "qty": 1,
            "success": False,
            "target_hit": False,
            "net_pnl": 1,
            "net_r": 0.1,
            "costs": 0.5,
            "session": "2026-09-16",
        }
    ]
    result = summarize(rows, ["2026-09-15", "2026-09-16"])
    assert result["success_rate"] == 0
    assert result["net_win_rate"] == 1
    assert result["no_call_sessions"] == 1


def test_null_uses_same_next_open_barriers_and_slippage():
    frame = pd.DataFrame(
        {
            "open": [101] * 5,
            "high": [110] * 5,
            "low": [100.5] * 5,
            "close": [109] * 5,
        },
        index=pd.date_range("2026-09-16 10:00", periods=5, freq="5min", tz="Asia/Kolkata"),
    )
    slip = 0.0005
    entry = 101 * (1 + slip)
    stop, target = entry - 1, entry + 2
    gross = target * (1 - slip) - entry
    trade = replace(
        _candidate(),
        entry=entry,
        stop=stop,
        target=target,
        gross_r=gross,
        session_end=4,
        exit=target * (1 - slip),
    )
    result = matched_random([trade], {"NSE_1": frame}, n_cohorts=20, slippage=slip)
    assert result["gross_r_lift"] == pytest.approx(0, abs=1e-10)
    assert result["null_mean_gross_r"] == pytest.approx(gross)


def test_offline_report_uses_own_namespace_and_excludes_incomplete_sessions(tmp_path, monkeypatch):
    from tradedesk_lab import intraday_research as research

    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config/sector_membership.yaml").write_text("BANK: [TEST]\n", encoding="utf-8")
    risk = RiskConfig(trading_capital=Decimal("100000"))
    monkeypatch.setattr(research, "load_config", lambda root: SimpleNamespace(risk=risk))
    idx = pd.date_range("2026-09-15 09:15", periods=75, freq="5min", tz="Asia/Kolkata")
    idx = idx.append(pd.date_range("2026-09-16 09:15", periods=10, freq="5min",
                                  tz="Asia/Kolkata"))
    with duckdb.connect(str(tmp_path / "data/tradedesk.duckdb")) as con:
        con.execute("CREATE TABLE candles(scrip_code VARCHAR, interval VARCHAR, ts BIGINT, "
                    "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)")
        con.execute("CREATE TABLE instruments(scrip_code VARCHAR, trading_symbol VARCHAR)")
        con.execute("INSERT INTO instruments VALUES ('NSE_1', 'TEST')")
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)", [
            ("NSE_1", Interval.M5.value, int(stamp.timestamp()), 100, 101, 99, 100.5, 1000)
            for stamp in idx
        ])
    output = tmp_path / "data/research"
    output.mkdir()
    primary = output / "latest.json"
    primary.write_text('{"protected": true}', encoding="utf-8")
    report = research.run_research(tmp_path, output, sessions=2, n_cohorts=20)
    assert report["status"] == "historical_diagnostic_only"
    assert report["eligible_for_live"] is False
    assert report["data_quality"] == {
        "complete_symbol_sessions": 1,
        "skipped_incomplete_or_nonstandard_symbol_sessions": 1,
    }
    assert (output / "intraday_research" / report["run_id"] / "report.json").exists()
    assert (output / "intraday_research/latest.json").exists()
    assert primary.read_text(encoding="utf-8") == '{"protected": true}'
    assert not any(k in INTRADAY_REGISTRY for k in ResearchSetupKind)
