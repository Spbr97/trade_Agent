"""M6: scoring, filters, watchlist build, parity with the backtester, report, charts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.backtest.test_runner import CAL, PARAMS, REF, RULES, build_store, config
from tradedesk.alerts.charts import render_signal_chart
from tradedesk.backtest.runner import prepare_market, run_backtest
from tradedesk.config import load_config
from tradedesk.config.models import RiskConfig, Settings, UniverseConfig
from tradedesk.engine.filters import apply_filters
from tradedesk.engine.lifecycle import SignalState
from tradedesk.engine.scoring import (
    EligibilityPolicy,
    Grade,
    Score,
    ScoreInputs,
    TrackRecord,
    eligibility,
    no_evidence,
    score_signal,
)
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.markets import EquityCostModel
from tradedesk.scan import (
    OpenPositionInfo,
    build_watchlist,
    load_watchlist,
    render_markdown,
    render_text,
    save_watchlist,
    scan_config,
    trade_card,
)

ROOT = Path(__file__).resolve().parents[2]


def sig(**kw: object) -> Signal:
    base = dict(
        id="s", scrip_code="NSE_1", symbol="ONE", setup=SetupKind.BASE_BREAKOUT,
        armed_on=date(2026, 3, 2), trigger=100.0, stop=95.0, t1=110.0, t2=115.0, atr=2.0,
    )  # fmt: skip
    base.update(kw)
    return Signal(**base)  # type: ignore[arg-type]


# ----------------------------------------------------------------- scoring


def test_score_grades_and_components() -> None:
    strong = score_signal(
        ScoreInputs(
            signal=sig(),
            trend_strength=1.0,
            rs_percentile=95,
            sector_percentile=90,
            pattern_quality=0.9,
            room_r=4.0,
            net_rr_t2=3.5,
            regime="risk_on",
        )  # fmt: skip
    )
    # A perfect-looking signal is still NOT alertable on its own: the setup behind it has no
    # proven track record, and NO TRADE is the default answer (SDD sections 1 and 23). Before
    # the 2026-09-13 eligibility gate this asserted `strong.alertable` directly, which is
    # exactly the fail-open that let all three live setups alert on an empty paper book.
    assert strong.grade is Grade.A and strong.total >= 80
    assert not strong.alertable
    assert any("no track record" in n for n in strong.notes)
    assert set(strong.components) == {
        "trend",
        "rs",
        "sector",
        "pattern",
        "room",
        "net_rr",
        "regime",
        "track",
    }
    weak = score_signal(
        ScoreInputs(
            signal=sig(),
            trend_strength=0.3,
            rs_percentile=55,
            sector_percentile=None,
            pattern_quality=0.3,
            room_r=1.0,
            net_rr_t2=1.6,
            regime="neutral",
        )  # fmt: skip
    )
    assert weak.grade is Grade.C and not weak.alertable
    assert any("overhead" in n for n in weak.notes) and any("net R:R" in n for n in weak.notes)
    benched = score_signal(
        ScoreInputs(
            signal=sig(),
            trend_strength=1.0,
            rs_percentile=95,
            sector_percentile=90,
            pattern_quality=0.9,
            room_r=None,
            net_rr_t2=3.5,
            regime="risk_on",
            track=TrackRecord(trades=40, expectancy_r=-0.2, benched=True),
        )  # fmt: skip
    )
    assert benched.grade is Grade.A and benched.benched and not benched.alertable
    assert benched.components["track"] < 0.5


# ------------------------------------------------------- eligibility gate (SDD 18/23)


def _proven(**over: object) -> TrackRecord:
    """A setup that clears every default requirement, so each test below can knock out
    exactly one criterion and prove that criterion is what does the rejecting."""
    base: dict[str, object] = dict(
        trades=600, oos_trades=150, win_rate=0.85, expectancy_r=0.40,
        random_baseline_r=0.10, benched=False,
    )  # fmt: skip
    base.update(over)
    ok, reasons = eligibility(
        trades=int(base["trades"]), oos_trades=int(base["oos_trades"]),
        win_rate=float(base["win_rate"]), expectancy_r=float(base["expectancy_r"]),
        random_baseline_r=base["random_baseline_r"],  # type: ignore[arg-type]
    )  # fmt: skip
    return TrackRecord(
        trades=int(base["trades"]), expectancy_r=float(base["expectancy_r"]),
        win_rate=float(base["win_rate"]), benched=bool(base["benched"]),
        oos_trades=int(base["oos_trades"]),
        random_baseline_r=base["random_baseline_r"],  # type: ignore[arg-type]
        eligible=ok, ineligibility_reasons=reasons,
    )  # fmt: skip


def _score_with(track: TrackRecord) -> Score:
    return score_signal(
        ScoreInputs(
            signal=sig(), trend_strength=1.0, rs_percentile=95, sector_percentile=90,
            pattern_quality=0.9, room_r=4.0, net_rr_t2=3.5, regime="risk_on", track=track,
        )  # fmt: skip
    )


def test_a_setup_with_no_evidence_is_ineligible_with_reasons() -> None:
    """The fail-open this gate closes: before 2026-09-13 a setup with an empty paper book
    was never benched (bench needs 30 resolved trades first), so it alerted freely."""
    track = no_evidence()
    assert not track.eligible
    assert track.ineligibility_reasons  # must SAY why, not fail silently
    score = _score_with(track)
    assert not score.alertable
    assert any("resolved trades" in r for r in score.no_trade_reasons)
    assert any("random-timing baseline" in r for r in score.no_trade_reasons)


def test_a_fully_proven_setup_is_eligible_and_alertable() -> None:
    score = _score_with(_proven())
    assert score.alertable and score.no_trade_reasons == ()


@pytest.mark.parametrize(
    ("override", "expect"),
    [
        ({"trades": 100}, "resolved trades"),
        ({"oos_trades": 10}, "out-of-sample"),
        ({"win_rate": 0.55}, "win rate"),
        ({"expectancy_r": -0.10}, "expectancy"),
        ({"random_baseline_r": None}, "random-timing baseline"),
    ],
)
def test_each_criterion_alone_makes_a_setup_ineligible(override: dict, expect: str) -> None:
    score = _score_with(_proven(**override))
    assert not score.alertable
    assert any(expect in r for r in score.no_trade_reasons), score.no_trade_reasons


def test_beating_the_market_is_not_enough_it_must_beat_RANDOM() -> None:
    """The criterion that catches this project's actual failure. A setup can post a healthy
    +0.30R expectancy and a good win rate while still being WORSE than picking a random day
    on the same stock - which is exactly what all three live setups were measured to be
    (0.21-0.26R worse than random, 2026-09-13). A win-rate threshold cannot see that."""
    flattering = _proven(expectancy_r=0.30, win_rate=0.85, random_baseline_r=0.35)
    assert not flattering.eligible
    assert any("beats random by only" in r for r in flattering.ineligibility_reasons)
    assert not _score_with(flattering).alertable


def test_score_floor_is_enforced_separately_from_the_grade() -> None:
    """Grade A starts at 80 but the SDD's floor is 85 - a proven setup scoring 82 is still
    NO TRADE, so the floor cannot be satisfied by the grade boundary alone."""
    track = _proven()
    high = _score_with(track)
    assert high.alertable
    low = score_signal(
        ScoreInputs(
            signal=sig(), trend_strength=0.75, rs_percentile=80, sector_percentile=70,
            pattern_quality=0.7, room_r=2.5, net_rr_t2=2.6, regime="risk_on", track=track,
        ),  # fmt: skip
        EligibilityPolicy(min_score=99),
    )
    assert not low.alertable
    assert any("< required 99" in r for r in low.no_trade_reasons)


# ----------------------------------------------------------------- filters


def test_filters_reasons_and_net_rr() -> None:
    risk = RiskConfig(trading_capital=100000)  # type: ignore[arg-type]
    uni = UniverseConfig()
    costs = EquityCostModel(risk.costs)
    common = dict(
        costs=costs,
        min_net_rr=risk.min_net_rr,
        min_avg_daily_turnover_inr=uni.min_avg_daily_turnover_inr,
        atr_pct_band=uni.atr_pct_band,
    )
    ok = apply_filters(sig(), qty=50, atr_pct=2.5, avg_turnover=1e8, regime="risk_on", **common)  # type: ignore[arg-type]
    assert ok.ok and ok.net_rr_t2 is not None and ok.net_rr_t2 > 2.0 and ok.net_rr_t1 is not None
    bad = apply_filters(
        sig(t2=104.0), qty=50, atr_pct=7.0, avg_turnover=1e6, regime="risk_off",
        surveillance={"ONE": "ASM"}, upper_circuit=100.2, **common,
    )  # type: ignore[arg-type]  # fmt: skip
    assert not bad.ok
    joined = " ".join(bad.reasons)
    for word in ("turnover", "ATR", "ASM", "circuit", "risk_off", "net R:R"):
        assert word in joined, word
    none = apply_filters(sig(), qty=0, atr_pct=None, avg_turnover=None, regime=None, **common)  # type: ignore[arg-type]
    assert none.ok and none.net_rr_t2 is None


# ---------------------------------------------------------------- scan_config


def _with_all_setups_disabled(settings: Settings) -> Settings:
    """config/setups.yaml has flipped between all-disabled and all-enabled over this
    project's life (see the 2026-09-13 fallback-bug note below) - tests that need the
    "nothing enabled" case build it explicitly rather than depending on the live config's
    current state."""
    disabled = {k: v.model_copy(update={"enabled": False}) for k, v in settings.setups.setups.items()}  # noqa: E501
    return settings.model_copy(update={"setups": settings.setups.model_copy(update={"setups": disabled})})  # noqa: E501


def test_no_enabled_setups_finds_nothing_when_fallback_is_off() -> None:
    """Real bug (2026-09-13): config/setups.yaml originally marked every setup `enabled:
    false` ("ships disabled until reviewed"), but the live `tradedesk scan` scheduled task
    called scan_config with no explicit --setup, and the old code silently fell back to
    running every setup regardless of that flag - so "disabled" never actually meant
    disabled in production (all three were live and alerting the whole time; the config was
    later explicitly reviewed and re-enabled). `fallback_to_all_if_none_enabled=False`
    (what the live `scan` CLI command now passes) must leave `cfg.setups` empty when
    nothing is enabled, not quietly substitute every SetupKind."""
    settings = _with_all_setups_disabled(load_config(ROOT))
    assert not any(v.enabled for v in settings.setups.setups.values())
    cfg = scan_config(settings, date(2026, 3, 2), fallback_to_all_if_none_enabled=False)
    assert cfg.setups == []


def test_no_enabled_setups_still_runs_everything_by_default() -> None:
    """The permissive default stays for backtest/train/mcp callers - omitting --setup on a
    RESEARCH tool reasonably means "try every setup", unlike the live scan path above."""
    settings = _with_all_setups_disabled(load_config(ROOT))
    cfg = scan_config(settings, date(2026, 3, 2))
    assert set(cfg.setups) == set(SetupKind)


def test_explicit_setup_list_is_respected_regardless_of_fallback_flag() -> None:
    settings = load_config(ROOT)
    cfg = scan_config(
        settings, date(2026, 3, 2), [SetupKind.BASE_BREAKOUT],
        fallback_to_all_if_none_enabled=False,
    )  # fmt: skip
    assert cfg.setups == [SetupKind.BASE_BREAKOUT]


# ------------------------------------------------------------ watchlist e2e


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    stocks = {"NSE_WIN": ("win", 1), "NSE_GAP": ("gap", 2), "NSE_STOP": ("stop", 3)}
    store = build_store(stocks)
    cfg = config(CAL[300 - 40], CAL[-1])
    md = prepare_market(store, list(stocks), REF, cfg)
    res = run_backtest(md, cfg)
    settings = load_config(ROOT)
    return store, cfg, md, res, settings


def _settings_for(settings: Settings, cfg) -> Settings:  # type: ignore[no-untyped-def]
    # Use the test's relaxed setup params and a universe rule that admits the synthetic stocks.
    setups = settings.setups.model_copy(
        update={
            "setups": {
                "base_breakout": settings.setups.setups["base_breakout"].model_copy(
                    update=PARAMS["base_breakout"]
                )
            }
        }
    )
    universe = settings.universe.model_copy(
        update={
            "min_avg_daily_turnover_inr": RULES.min_avg_turnover_inr,
            "min_price": RULES.min_price,
        }
    )
    return settings.model_copy(update={"setups": setups, "universe": universe})


def test_watchlist_matches_backtester_signals_for_that_date(world) -> None:  # type: ignore[no-untyped-def]
    store, cfg, md, res, settings = world
    armed_by_date: dict[date, set[str]] = {}
    for ts in res.signals:
        armed_by_date.setdefault(ts.signal.armed_on, set()).add(ts.signal.id)
    assert armed_by_date
    s = _settings_for(settings, cfg)
    for d, ids in sorted(armed_by_date.items())[:5]:
        # The backtester skips codes it is already tracking (armed or open) when it scans;
        # give the evening scan the same exclusion via open positions.
        live = {
            ts.signal.scrip_code
            for ts in res.signals
            if ts.signal.armed_on < d and (not ts.terminal or ts.history[-1].on > d)
        }
        held = [OpenPositionInfo(scrip_code=c, entry=1.0, stop=1.0, qty=0) for c in live]
        wl = build_watchlist(md, cfg, s, d, open_positions=held)
        assert {e.signal.id for e in wl.entries} == ids, d
        # Same levels, not just the same ids.
        levels = {ts.signal.id: (ts.signal.trigger, ts.signal.stop) for ts in res.signals}
        for e in wl.entries:
            assert (e.signal.trigger, e.signal.stop) == levels[e.signal.id]


def test_watchlist_entries_are_priced_scored_and_serialisable(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store, cfg, md, res, settings = world
    d = sorted({ts.signal.armed_on for ts in res.signals})[-1]
    wl = build_watchlist(md, cfg, _settings_for(settings, cfg), d)
    assert wl.entries
    e = wl.entries[0]
    assert e.qty > 0 and e.risk_pct <= float(cfg.risk.max_risk_per_trade_pct) + 1e-9
    assert e.costs_round_trip > 0 and e.net_rr_t2 is not None
    assert e.grade in (Grade.A, Grade.B, Grade.C) and 0 <= e.score <= 100
    assert wl.entries == sorted(wl.entries, key=lambda x: (-x.score, x.signal.scrip_code))
    text = render_text(wl)
    assert e.signal.symbol in text
    if wl.active:
        assert "Trigger" in text and "Qty" in text
    assert "|" in render_markdown(wl)
    card = trade_card(e, wl.capital)
    assert f"{e.signal.trigger:.2f}" in card and "net R:R" in card
    path = save_watchlist(wl, tmp_path / "wl")
    again = load_watchlist(path)
    assert again.on == wl.on and [x.signal.id for x in again.entries] == [
        x.signal.id for x in wl.entries
    ]


def test_open_positions_are_excluded_and_add_heat(world) -> None:  # type: ignore[no-untyped-def]
    store, cfg, md, res, settings = world
    d = sorted({ts.signal.armed_on for ts in res.signals})[-1]
    s = _settings_for(settings, cfg)
    base = build_watchlist(md, cfg, s, d)
    code = base.entries[0].signal.scrip_code
    held = [OpenPositionInfo(scrip_code=code, entry=100.0, stop=90.0, qty=200)]  # 2,000 risk
    wl = build_watchlist(md, cfg, s, d, open_positions=held)
    assert all(e.signal.scrip_code != code for e in wl.entries)
    assert all(e.heat_before_pct == pytest.approx(2000 / cfg.capital) for e in wl.entries)


def test_chart_renders_png(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store, cfg, md, res, settings = world
    ts = next(t for t in res.signals if t.state is not SignalState.ARMED)
    code = ts.signal.scrip_code
    feats = md.features[code].iloc[: md.pos_by_date[code][ts.signal.armed_on] + 1]
    out = render_signal_chart(feats, ts.signal, tmp_path / "charts" / "x.png")
    assert out.exists() and out.stat().st_size > 10_000
