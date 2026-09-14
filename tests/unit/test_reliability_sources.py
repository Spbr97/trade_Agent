"""Per-market glue tested against real fixtures (JSONL rows, a real Journal/paper_trades
table) rather than structural properties - see reliability_sources.py's module docstring
for why the two test files use different styles."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.config.models import RiskConfig
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.journal import Journal
from tradedesk.markets import EquityCostModel
from tradedesk.reliability_sources import (
    crypto_bse_backfill_pnl,
    crypto_bse_live_counts,
    crypto_bse_symbol_confidence,
    log_daily_reliability_snapshot,
    nse_live_counts,
    nse_symbol_confidence,
    overall_reliability_now,
)

RISK = RiskConfig(trading_capital=Decimal("1000000"))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _call_row(
    symbol: str, *, label: int, source: str, r_multiple: float = 1.0, resolved_at: str = "2026-09-01T00:00:00"  # noqa: E501
) -> dict:
    return {
        "signal_id": f"{symbol}:{resolved_at}", "symbol": symbol, "label": label,
        "source": source, "r_multiple": r_multiple, "resolved_at": resolved_at,
    }  # fmt: skip


def test_crypto_bse_symbol_confidence_pools_or_filters_by_source(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [
        _call_row("BTC", label=1, source="backfill"),
        _call_row("BTC", label=0, source="backfill"),
        _call_row("BTC", label=1, source="live"),
        _call_row("ETH", label=0, source="live"),
        {"signal_id": "x", "symbol": "BTC", "label": None, "source": "live"},  # unresolved
    ])  # fmt: skip
    pooled = {s.symbol: s for s in crypto_bse_symbol_confidence(log)}
    assert pooled["BTC"].n == 3 and pooled["BTC"].wins == 2
    live_only = {s.symbol: s for s in crypto_bse_symbol_confidence(log, source="live")}
    assert live_only["BTC"].n == 1 and live_only["BTC"].wins == 1
    assert live_only["ETH"].n == 1 and live_only["ETH"].wins == 0


def test_crypto_bse_live_counts_ignores_backfill_and_unresolved(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [
        _call_row("BTC", label=1, source="live"),
        _call_row("BTC", label=0, source="live"),
        _call_row("BTC", label=1, source="backfill"),
        {"signal_id": "y", "symbol": "BTC", "label": None, "source": "live"},
    ])  # fmt: skip
    wins, n = crypto_bse_live_counts(log)
    assert (wins, n) == (1, 2)


def test_crypto_bse_live_counts_empty_log_is_zero_zero(tmp_path: Path) -> None:
    assert crypto_bse_live_counts(tmp_path / "missing.jsonl") == (0, 0)


def test_backfill_pnl_summary_only_counts_backfill_rows(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [
        _call_row("BTC", label=1, source="backfill", r_multiple=2.0),
        _call_row("BTC", label=0, source="backfill", r_multiple=-1.0),
        _call_row("BTC", label=1, source="live", r_multiple=3.0),  # excluded: not backfill
    ])  # fmt: skip
    summary = crypto_bse_backfill_pnl(log)
    assert summary["n"] == 2
    assert summary["sum_r"] == 1.0
    assert summary["avg_r"] == 0.5
    assert summary["win_rate"] == 0.5
    assert summary["pnl_pct"] == 1.0 * 0.0025 * 100


def test_backfill_pnl_summary_respects_the_timeframe_window(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write_jsonl(log, [
        _call_row("BTC", label=1, source="backfill", r_multiple=1.0, resolved_at="2020-01-01T00:00:00"),  # noqa: E501
        _call_row("BTC", label=1, source="backfill", r_multiple=1.0, resolved_at=date.today().isoformat()),  # noqa: E501
    ])  # fmt: skip
    all_time = crypto_bse_backfill_pnl(log, days=None)
    recent = crypto_bse_backfill_pnl(log, days=7)
    assert all_time["n"] == 2
    assert recent["n"] == 1


def test_backfill_pnl_summary_empty_log(tmp_path: Path) -> None:
    summary = crypto_bse_backfill_pnl(tmp_path / "missing.jsonl")
    assert summary == {"n": 0, "sum_r": 0.0, "avg_r": None, "win_rate": None, "pnl_pct": 0.0, "days": None}  # noqa: E501


def _closed_paper_trade(j: Journal, i: int, symbol: str, r: float, *, entry_on: date = date(2026, 1, 5)) -> None:  # noqa: E501
    s = Signal(
        id=f"base_breakout:NSE_{i}:{entry_on.isoformat()}", scrip_code=f"NSE_{i}", symbol=symbol,
        setup=SetupKind("base_breakout"), armed_on=entry_on, trigger=100.0, stop=95.0, t1=110.0,
        t2=115.0, atr=2.0,
    )  # fmt: skip
    pf = Portfolio(risk=RISK, costs=EquityCostModel(RISK.costs), equity=1_000_000.0)
    pos = Position(
        signal=s, entry_date=entry_on, entry_price=100.0, qty_initial=100, qty_open=0, stop=95.0,
        highest_close=100.0, sessions_held=2,
        fills=[Fill(on=entry_on, price=100.0, qty=100, reason=FillReason.ENTRY),
               Fill(on=entry_on, price=100.0 + 5 * r, qty=100, reason=FillReason.TRAIL)],
    )  # fmt: skip
    j.record_trade(pf.settle(pos), source="paper")


def test_nse_symbol_confidence_and_live_counts_from_a_real_paper_book(tmp_path: Path) -> None:
    jpath = tmp_path / "journal.sqlite"
    with Journal(jpath) as j:
        _closed_paper_trade(j, 1, "RELIANCE", r=+1.0)
        _closed_paper_trade(j, 2, "RELIANCE", r=-1.0)
        _closed_paper_trade(j, 3, "TCS", r=+1.0)
    ranked = {s.symbol: s for s in nse_symbol_confidence(jpath)}
    assert ranked["RELIANCE"].n == 2 and ranked["RELIANCE"].wins == 1
    assert ranked["TCS"].n == 1 and ranked["TCS"].wins == 1
    wins, n = nse_live_counts(jpath)
    assert (wins, n) == (2, 3)


def test_nse_reads_return_empty_when_no_journal_file_exists(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sqlite"
    assert nse_symbol_confidence(missing) == []
    assert nse_live_counts(missing) == (0, 0)


def test_overall_reliability_now_pools_all_three_markets(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import tradedesk.reliability_sources as rs

    jpath = tmp_path / "journal.sqlite"
    with Journal(jpath) as j:
        _closed_paper_trade(j, 1, "RELIANCE", r=+1.0)
    crypto_log = tmp_path / "crypto.jsonl"
    bse_log = tmp_path / "bse.jsonl"
    _write_jsonl(crypto_log, [_call_row("BTC", label=1, source="live")])
    _write_jsonl(bse_log, [_call_row("SENSEX", label=0, source="live")])
    monkeypatch.setattr(rs, "NSE_JOURNAL", jpath)
    monkeypatch.setattr(rs, "CRYPTO_LOG", crypto_log)
    monkeypatch.setattr(rs, "BSE_LOG", bse_log)

    result = overall_reliability_now()
    assert result["n"] == 3 and result["wins"] == 2
    assert result["by_market"]["nse"]["n"] == 1
    assert result["by_market"]["crypto"]["wins"] == 1
    assert result["by_market"]["bse"]["wins"] == 0


def test_daily_snapshot_is_idempotent_within_a_day(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import tradedesk.reliability_sources as rs

    monkeypatch.setattr(rs, "NSE_JOURNAL", tmp_path / "nope.sqlite")
    monkeypatch.setattr(rs, "CRYPTO_LOG", tmp_path / "nope.jsonl")
    monkeypatch.setattr(rs, "BSE_LOG", tmp_path / "nope.jsonl")
    history_path = tmp_path / "history.jsonl"

    first = log_daily_reliability_snapshot(history_path)
    second = log_daily_reliability_snapshot(history_path)
    assert first is not None
    assert second is None  # already logged today - no duplicate row
    rows = history_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
