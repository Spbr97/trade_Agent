"""tradedesk_lab/candidates/rules.py: the replacement search's strategy grammar.

The leakage test is the one CLAUDE.md requires of every signal: each trigger/filter,
evaluated on the full history, must give exactly the value it gives on the history cut off
at that bar - and on just the TAIL_BARS tail the production module evaluates."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.candidates import candidate_space
from tradedesk_lab.candidates import rules as R

from tradedesk.engine.indicators import daily_features
from tradedesk.engine.signals import SetupKind
from tradedesk.setups import REGISTRY
from tradedesk.setups.base import SetupContext


def _frame(n: int = 700, seed: int = 3, regime: int = 90) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n, tz="Asia/Kolkata")
    # Regime-switching drift so crosses, breakouts, squeezes and dips all actually occur.
    drift = np.where((np.arange(n) // regime) % 2 == 0, 0.002, -0.0015)
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.018, n)))
    high = close * (1 + np.abs(rng.normal(0.008, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0.008, 0.004, n)))
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(50_000, 400_000, n).astype(float)
    volume[rng.random(n) < 0.05] *= 4  # occasional surges, as real volume has
    raw = pd.DataFrame(
        {"open": open_, "high": np.maximum(high, open_), "low": np.minimum(low, open_),
         "close": close, "volume": volume},
        index=idx,
    )  # fmt: skip
    return daily_features(raw)


FULL = _frame()
CHECK_AT = list(range(R.WARMUP_BARS, len(FULL), 23))


@pytest.mark.parametrize("name", list(R.TRIGGERS) + [f"filter:{f}" for f in R.FILTERS])
def test_no_look_ahead_and_tail_equals_full(name: str) -> None:
    if name.startswith("filter:"):
        fn = R._FILTER_FNS[name.split(":", 1)[1]]
    else:
        fn = R._TRIGGER_FNS[name]
    full = R.evaluate(fn, FULL)
    for i in CHECK_AT:
        prefix = FULL.iloc[: i + 1]
        assert bool(R.evaluate(fn, prefix).iloc[-1]) == bool(full.iloc[i]), (name, i)
        tail = prefix.iloc[-R.TAIL_BARS :]
        assert bool(R.evaluate(fn, tail).iloc[-1]) == bool(full.iloc[i]), (name, i)


def test_every_trigger_fires_at_least_once_on_realistic_data() -> None:
    # A trigger that can never fire would silently waste search budget. The long-regime
    # frame has year-long bear phases, so 12-month momentum can turn negative and back.
    long_regime = _frame(n=1200, regime=300)
    for name, fn in R._TRIGGER_FNS.items():
        assert R.evaluate(fn, FULL).any() or R.evaluate(fn, long_regime).any(), name


def test_prepared_column_and_on_the_fly_arm_agree() -> None:
    ctx = SetupContext(scrip_code="X", symbol="X")
    cand = R.RuleCandidate("donchian20", "any", "trend_trail")
    prepared = cand.prepare(FULL)
    fired = 0
    for i in CHECK_AT:
        a = cand.arm(prepared.iloc[: i + 1], ctx, {})
        b = cand.arm(FULL.iloc[: i + 1], ctx, {})
        assert (a is None) == (b is None)
        if a is not None:
            fired += 1
            assert a.trigger == b.trigger and a.stop == b.stop and a.t1 == b.t1
    assert cand.gate_column in prepared.columns


def _exec_production(source: str) -> dict:
    namespace: dict = {}
    exec(compile(source, "<generated>", "exec"), namespace)  # noqa: S102 - test of our own template
    return namespace


@pytest.mark.parametrize(
    "candidate",
    [
        R.RuleCandidate("donchian20", "any", "trend_trail"),
        R.RuleCandidate("rsi2_dip", "above_ema200", "revert_2r"),
        R.RuleCandidate("ibs_low", "any", "revert_1r"),
        R.RuleCandidate("high52_break", "any", "trend_trail"),
        R.ImprovedSetup("nr7_breakout", "any", "target_3r", base_params={"enabled": True}),
    ],
    ids=lambda c: c.name,
)
def test_production_source_arms_exactly_what_the_lab_candidate_arms(candidate) -> None:
    # Generated against an EXISTING enum member so it can be executed here; apply() adds
    # the real member in the same commit that writes the file.
    ns = _exec_production(candidate.production_source("BASE_BREAKOUT"))
    cls = ns[R._class_name(candidate.name)]
    prod = cls()
    ctx = SetupContext(scrip_code="X", symbol="X")
    compared = 0
    for i in range(R.WARMUP_BARS, len(FULL), 7):
        df = FULL.iloc[: i + 1]
        lab = candidate.arm(df, ctx, {})
        live = prod.arm(df, ctx, {})
        assert (lab is None) == (live is None), (candidate.name, i)
        if lab is not None:
            compared += 1
            assert live.trigger == pytest.approx(lab.trigger)
            assert live.stop == pytest.approx(lab.stop)
            assert live.t1 == pytest.approx(lab.t1)
            assert live.exit_plan == lab.exit_plan
    assert compared > 0, "no signals compared - the test data never armed this candidate"


def test_improvement_variant_reuses_the_production_pattern_and_the_cache_is_transparent() -> None:
    ctx = SetupContext(scrip_code="X", symbol="X")
    params = {"enabled": True}
    base = REGISTRY[SetupKind.NR7_BREAKOUT]
    R.clear_base_cache()
    for i in CHECK_AT:
        df = FULL.iloc[: i + 1]
        direct = base.arm(df, ctx, params)
        cached_first = R._cached_base_arm("nr7_breakout", params, df, ctx)
        cached_again = R._cached_base_arm("nr7_breakout", params, df, ctx)
        assert (direct is None) == (cached_first is None) == (cached_again is None)
        if direct is not None:
            assert cached_again.trigger == direct.trigger and cached_again.stop == direct.stop


def test_candidate_space_puts_research_backed_rules_first_and_improvements_last() -> None:
    space = candidate_space(["base_breakout"], {"base_breakout": {"enabled": True}})
    names = [c.name for c in space]
    assert names[0].startswith("r_high52_break_")
    assert len(names) == len(set(names))  # every name unique - the search's memory key
    n_improve = len(R.FILTERS) * 2
    assert all(n.startswith("imp_base_breakout_") for n in names[-n_improve:])
    assert "mean_reversion_v1" in names
    assert any(n.startswith("r_donchian55_") for n in names)
    research = [t for t in R.TRIGGERS if t in R.EVIDENCE][:6]
    first_textbook = next(i for i, n in enumerate(names) if n.startswith("r_donchian20_"))
    assert all(
        max(i for i, n in enumerate(names) if n.startswith(f"r_{t}_")) < first_textbook
        for t in research
    )
    # Names must survive apply()'s enum/class/file-name derivation.
    for name in names:
        assert name.isidentifier() and name == name.lower()


def test_every_evidence_entry_names_a_real_trigger_or_filter() -> None:
    assert set(R.EVIDENCE) <= set(R.TRIGGERS) | set(R.FILTERS)
