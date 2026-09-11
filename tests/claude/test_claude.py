"""M10: Claude is advisory only.

- Schema test: no output model has a numeric field; applying reads cannot change a price,
  level or quantity, cannot raise a grade, cannot add an entry.
- Trigger notes never delay the alert: a slow advisor does not slow `on_alert`.
- Weekly review gathers from the journal and renders; MCP tools return JSON.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel

from tradedesk.broker.indstocks.models import IST
from tradedesk.claude import ChartRead, ClaudeAdvisor, TriggerNote, WeeklyReview
from tradedesk.claude.chart_read import apply_reads, read_watchlist, setup_facts
from tradedesk.claude.models import TEXT_ONLY_MODELS
from tradedesk.claude.trigger_note import TriggerNoteWorker, bar_summary
from tradedesk.claude.weekly_review import gather, render, run_weekly_review, week_bounds
from tradedesk.config.models import ClaudeConfig
from tradedesk.engine.regime import Regime, RegimeSnapshot
from tradedesk.engine.scoring import Grade
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.journal import Journal
from tradedesk.live.models import Alert, AlertKind, AlertLevel, IntradayBar
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry

D = date(2026, 3, 2)


def sig(code: str = "NSE_1", trigger: float = 100.0) -> Signal:
    return Signal(
        id=f"base_breakout:{code}:2026-03-01", scrip_code=code, symbol=code.replace("NSE_", "S"),
        setup=SetupKind.BASE_BREAKOUT, armed_on=D - timedelta(days=1), trigger=trigger, stop=95.0,
        t1=110.0, t2=115.0, atr=2.0, reasons=["15-bar base"], rs_percentile=88.0, regime="risk_on",
    )  # fmt: skip


def entry(code: str = "NSE_1", grade: Grade = Grade.A, score: int = 85) -> WatchlistEntry:
    return WatchlistEntry(
        signal=sig(code), score=score, grade=grade, score_components={"trend": 1.0}, score_notes=[],
        alertable=grade is not Grade.C, qty=50, risk_amount=250.0, risk_pct=0.0025,
        position_value=5000.0, size_caps=[], costs_round_trip=60.0, net_rr_t1=1.6, net_rr_t2=2.4,
        results_in_sessions=None, heat_before_pct=0.0, heat_after_pct=0.0025, atr_pct=2.0,
        avg_turnover=1e8,
    )  # fmt: skip


def watchlist(*entries: WatchlistEntry) -> Watchlist:
    regime = RegimeSnapshot(
        on=D, regime=Regime.RISK_ON, benchmark_close=1.0, benchmark_ema=1.0, ema_rising=True,
        above_ema=True, breadth_pct=60.0, vix=12.0, vix_change_5d_pct=0.0, size_multiplier=1.0,
        reasons=["fine"],
    )  # fmt: skip
    return Watchlist(
        on=D, generated_at=datetime.combine(D, dtime(16, 30), tzinfo=IST), regime=regime,
        capital=100000.0, entries=list(entries), open_positions=[],
    )  # fmt: skip


def read(verdict: str = "keep") -> ChartRead:
    return ChartRead(
        pattern_quality="Tight 15-bar base under a clean prior high.",
        overhead_supply="Little supply until the 52-week high, well above T2.",
        failure_condition="A close back inside the base on volume.",
        verdict=verdict,  # type: ignore[arg-type]
        confidence="medium",
    )


# ---------------------------------------------------------------- schema


def _leaf_types(annotation: Any) -> set[Any]:
    origin = get_origin(annotation)
    if origin is None:
        return {annotation}
    out: set[Any] = set()
    for a in get_args(annotation):
        if a is type(None) or isinstance(a, types.NoneType.__class__):
            continue
        out |= _leaf_types(a)
    return out


def test_claude_output_schemas_have_no_numeric_fields() -> None:
    for model in TEXT_ONLY_MODELS:
        assert issubclass(model, BaseModel)
        for name, field in model.model_fields.items():
            leaves = _leaf_types(field.annotation)
            for leaf in leaves:
                assert leaf not in (int, float, complex), f"{model.__name__}.{name} is numeric"
                assert not (isinstance(leaf, type) and issubclass(leaf, BaseModel)), name
        schema = json.dumps(model.model_json_schema())
        assert '"type": "number"' not in schema and '"type": "integer"' not in schema
        assert model.model_config.get("extra") == "forbid"


def test_apply_reads_cannot_change_prices_quantities_or_raise_grades() -> None:
    wl = watchlist(
        entry("NSE_1", Grade.A, 85), entry("NSE_2", Grade.B, 70), entry("NSE_3", Grade.C, 50)
    )
    reads = {
        wl.entries[0].signal.id: read("downgrade"),
        wl.entries[1].signal.id: read("remove"),
        wl.entries[2].signal.id: read("keep"),
    }
    for mode in ("off", "notify", "veto"):
        out = apply_reads(wl, reads, mode)
        assert len(out.entries) == len(wl.entries)  # never adds or drops an entry
        by_id = {e.signal.id: e for e in out.entries}
        for before in wl.entries:
            after = by_id[before.signal.id]
            assert after.signal == before.signal  # trigger, stop, T1, T2, ATR untouched
            assert (after.qty, after.risk_amount, after.position_value) == (
                before.qty, before.risk_amount, before.position_value,
            )  # fmt: skip
            assert after.score == before.score
            assert (
                Grade[after.grade.value] >= Grade[before.grade.value]
                or after.grade.value >= before.grade.value
            )
    veto = {e.signal.id: e for e in apply_reads(wl, reads, "veto").entries}
    assert veto[wl.entries[0].signal.id].grade is Grade.B  # A -> B
    assert (
        veto[wl.entries[1].signal.id].rejected_for
        and "Claude veto" in veto[wl.entries[1].signal.id].rejected_for[0]
    )
    assert veto[wl.entries[2].signal.id].grade is Grade.C
    notify = {e.signal.id: e for e in apply_reads(wl, reads, "notify").entries}
    assert notify[wl.entries[0].signal.id].grade is Grade.A  # notify never changes grades
    assert all(n.startswith("Claude (") for e in notify.values() for n in e.score_notes)
    assert apply_reads(wl, reads, "off") is wl
    # A 'keep' verdict can never promote: C stays C under every mode.
    assert all(
        {e.signal.id: e for e in apply_reads(wl, reads, m).entries}[wl.entries[2].signal.id].grade
        is Grade.C
        for m in ("notify", "veto")
    )


# ---------------------------------------------------------------- advisor


def fake_parse(verdict: str = "keep", delay: float = 0.0):  # type: ignore[no-untyped-def]
    calls: list[tuple[str, Any]] = []

    def _parse(
        model: str, messages: list[dict[str, Any]], output: type[BaseModel]
    ) -> tuple[Any, dict[str, int]]:
        calls.append((model, messages))
        if delay:
            time.sleep(delay)
        usage = {"input_tokens": 1000, "output_tokens": 100}
        if output is ChartRead:
            return read(verdict), usage
        if output is TriggerNote:
            return TriggerNote(note="Buyers held the breakout level on each 15-minute dip."), usage
        return (
            WeeklyReview(
                summary="Quiet week.",
                what_went_well=["Stops honoured"],
                what_hurt=["One gap"],
                rule_breaks=[],
                one_change_next_week="Skip entries in the last hour.",
            ),  # fmt: skip
            usage,
        )

    return _parse, calls


def test_advisor_off_never_calls_and_budget_stops_calls(tmp_path: Path) -> None:
    parse, calls = fake_parse()
    off = ClaudeAdvisor(ClaudeConfig(mode="off"), usage_log=tmp_path / "u.jsonl", parse=parse)
    assert off.read_chart({}, None) is None and calls == []
    on = ClaudeAdvisor(
        ClaudeConfig(mode="notify", monthly_spend_limit_usd=0.004),
        usage_log=tmp_path / "u.jsonl",
        parse=parse,  # type: ignore[arg-type]
    )
    assert on.read_chart({"symbol": "S1"}, None) is not None and len(calls) == 1
    assert on.month_spend_usd() > 0
    # sonnet-5: 1000 in @ $2 + 100 out @ $10 = $0.003 -> second call crosses the $0.004 cap...
    assert on.read_chart({"symbol": "S1"}, None) is not None and len(calls) == 2
    assert on.over_budget() and on.read_chart({"symbol": "S1"}, None) is None and len(calls) == 2


def test_read_watchlist_sends_chart_and_facts(tmp_path: Path) -> None:
    parse, calls = fake_parse("downgrade")
    png = tmp_path / "c.png"
    png.write_bytes(b"\x89PNG fake")
    e = entry().model_copy(update={"chart_path": str(png)})
    adv = ClaudeAdvisor(ClaudeConfig(mode="veto"), usage_log=None, parse=parse)
    reads = read_watchlist(adv, watchlist(e, entry("NSE_2")), max_setups=1)
    assert list(reads) == [e.signal.id] and len(calls) == 1
    content = calls[0][1][0]["content"]
    assert any(b.get("type") == "image" for b in content)
    assert "Setup facts" in content[-1]["text"] and "S1" in content[-1]["text"]
    facts = setup_facts(e)
    assert facts["trigger"] == 100.0 and facts["setup"] == "base_breakout"


# ----------------------------------------------------------- trigger notes


async def test_trigger_note_never_delays_the_alert() -> None:
    parse, calls = fake_parse(delay=0.3)  # a slow model
    adv = ClaudeAdvisor(ClaudeConfig(mode="notify"), usage_log=None, parse=parse)
    delivered: list[Alert] = []
    e = entry()
    bars = [
        IntradayBar(
            scrip_code="NSE_1", start=datetime.combine(D, dtime(9, 30), tzinfo=IST),
            end=datetime.combine(D, dtime(9, 45), tzinfo=IST), open=99, high=101, low=98.5,
            close=100.9,
        )
    ]  # fmt: skip
    worker = TriggerNoteWorker(adv, watchlist(e), delivered.append, bars_for=lambda c: bars)
    alert = Alert(
        kind=AlertKind.TRIGGERED, level=AlertLevel.URGENT, at=datetime.now(IST), scrip_code="NSE_1",
        symbol="S1", message="TRIGGERED", payload={"signal_id": e.signal.id},
    )  # fmt: skip
    t0 = time.perf_counter()
    worker.on_alert(alert)  # the monitor's callback path
    assert time.perf_counter() - t0 < 0.05  # returned immediately, no API call yet
    assert calls == []
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    for _ in range(50):
        if delivered:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await task
    assert len(delivered) == 1 and delivered[0].kind is AlertKind.INFO
    assert (
        "Claude on the 15m chart" in delivered[0].message and delivered[0].payload["note"] is True
    )
    assert "09:30" in bar_summary(bars)
    assert len(calls) == 1
    # Non-trigger alerts are ignored entirely.
    worker.on_alert(alert.model_copy(update={"kind": AlertKind.NEAR_STOP}))
    assert worker.queue.empty()


# ----------------------------------------------------------- weekly review


def test_weekly_review_gathers_and_renders(tmp_path: Path) -> None:
    parse, calls = fake_parse()
    adv = ClaudeAdvisor(ClaudeConfig(mode="notify"), usage_log=None, parse=parse)
    today = datetime.now(IST).date()  # tags are stamped now, so review this week
    with Journal() as jn:
        jn.tag("base_breakout:NSE_1:2026-03-01", "rule_break", "entered at 09:20")
        stats, trades, tags = gather(jn, today)
        assert tags and tags[0]["tag"] == "rule_break" and trades == []
        path, review = run_weekly_review(adv, jn, today, tmp_path / "reviews")
    assert path is not None and review is not None and path.exists()
    text = path.read_text(encoding="utf-8")
    assert "One change next week" in text and "Skip entries in the last hour." in text
    assert week_bounds(D) == (date(2026, 3, 2), date(2026, 3, 8))
    assert render(review, today, stats).startswith("# Weekly review")
    off = ClaudeAdvisor(ClaudeConfig(mode="off"), usage_log=None, parse=parse)
    with Journal() as jn:
        assert run_weekly_review(off, jn, D, tmp_path / "r2") == (None, None)


# ------------------------------------------------------------------- MCP


def test_mcp_tools_are_registered_and_return_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tradedesk import mcp_server

    monkeypatch.setattr(mcp_server, "WATCHLISTS", tmp_path / "wl")
    monkeypatch.setattr(mcp_server, "JOURNAL", tmp_path / "j.sqlite")
    out = json.loads(mcp_server.get_watchlist())
    assert "error" in out
    from tradedesk.scan import save_watchlist

    save_watchlist(watchlist(entry()), tmp_path / "wl")
    out = json.loads(mcp_server.get_watchlist())
    assert out["entries"][0]["symbol"] == "S1" and out["entries"][0]["trigger"] == 100.0
    assert json.loads(mcp_server.get_position("NSE_1"))["info"].startswith("no open position")
    assert json.loads(mcp_server.journal_stats())["paper"]["trades"] == 0
    names = {t.name for t in mcp_server.server._tool_manager.list_tools()}
    assert names == {"get_watchlist", "get_position", "analyze", "journal_stats", "backtest"}
