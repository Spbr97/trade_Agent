"""Phase 3 orchestration: authors a lab candidate's validation run under the M14-M18
isolation contract, records it in `tradedesk_lab/registry.py`, and - only if it clears
the full harness gauntlet plus the same +0.10R-over-random margin every live setup's
`eligibility()` already requires - builds a `NewDetectorCode` proposal for
`decision_packet.submit_for_review`. A candidate that doesn't clear the gauntlet produces
no proposal at all, exactly like every other path in this feature.

`verify_base()` is checked before any authoring run, per this package's own contract.
Note (disclosed, not hidden): as of this module's introduction the recorded base manifest
(`data/m14_m18/base_manifest.json`) predates a full day of legitimate, explicitly
authorized production changes made earlier in this same session (the AEM v2 veto-layer
bug fixes and the self-review loop's own Phase 1/2 files), so `verify_base()` currently
reports real drift against that stale baseline. This module does NOT silently refresh the
manifest or bypass the check - `author_and_validate` raises, naming exactly what changed,
until a human decides to refresh the baseline (deleting and letting `protect()` rebuild
`base_manifest.json` from the current, now-legitimate state) via a separate, explicit
action - refreshing that baseline is a decision affecting all of M14-M18's drift
detection, not something this feature should make unilaterally.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, verify_base, write_json
from tradedesk_lab.candidates.base import LabSetup
from tradedesk_lab.harness.gauntlet import GauntletReport, run_gauntlet
from tradedesk_lab.harness.run_lab_candidate import simulate_single_setup, to_harness_trades
from tradedesk_lab.harness.spec import KillCriteria
from tradedesk_lab.registry import Registry

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.signals import SetupKind
from tradedesk.markets import Market, bse_market, crypto_market, nse_market
from tradedesk.proposals import NewDetectorCode


def _mean_reversion_v1_production_source(setup_kind_name: str) -> str:
    """The production-shaped counterpart of `tradedesk_lab/candidates/mean_reversion_v1.py`,
    using the real `make_signal()`/`Signal` (which requires a real `SetupKind` member -
    see `apply.py`'s NEW_DETECTOR case for how that member is added in the SAME commit
    that writes this file, never before). This is deliberately templated for this one
    candidate, not a generic lab-to-production transpiler - see this module's own
    docstring on scope."""

    return f'''"""Self-review Phase 3 candidate: rsi2<10 & close>ema50, the best-measured
gross-edge rule in this project's history (entry_search.py, +0.077R vs a random-timing
null, t=7.04), wrapped into the production Setup shape. See
docs/self-review-applied-log.md for the real gauntlet result that justified promoting
this from tradedesk_lab/candidates/mean_reversion_v1.py.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.setups.base import SetupContext, make_signal

STOP_ATR_MULT = 3.0
TARGET_R = 2.0
MAX_HOLD_SESSIONS = 10


class MeanReversionV1:
    kind = SetupKind.{setup_kind_name}

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None:
        if len(df) < 210:
            return None
        last = df.iloc[-1]
        if pd.isna(last.get("rsi2")) or pd.isna(last.get("ema50")) or pd.isna(last.get("atr14")):
            return None
        if not (last["rsi2"] < 10 and last["close"] > last["ema50"]):
            return None
        atr = float(last["atr14"])
        trigger = float(last["close"])
        if atr <= 0 or trigger <= 0:
            return None
        stop = trigger - float(params.get("stop_atr_mult", STOP_ATR_MULT)) * atr
        exit_plan = ExitPlan(
            partial_at_r=float(params.get("target_r", TARGET_R)),
            partial_fraction=1.0,
            trail="ema10_close",
            max_hold_sessions=int(params.get("max_hold_sessions", MAX_HOLD_SESSIONS)),
        )
        return make_signal(
            kind=self.kind,
            df=df,
            trigger=trigger,
            stop=stop,
            final_target=None,
            exit_plan=exit_plan,
            ctx=ctx,
            geometry={{"stop_atr_mult": STOP_ATR_MULT, "target_r": TARGET_R}},
            reasons=[f"rsi2={{last['rsi2']:.1f}} < 10, close above ema50"],
        )
'''


PRODUCTION_SOURCE_GENERATORS: dict[str, Any] = {
    "mean_reversion_v1": _mean_reversion_v1_production_source,
}

MUST_BEAT_RANDOM_BY_R = 0.10
DEFAULT_KILL_CRITERIA = KillCriteria(
    min_net_expectancy_r=0.0, min_sample_size=300, max_drawdown_pct=0.30, max_losing_streak=10
)
# NSE/BSE: never a lower sample bar than a human-authored setup must clear
# (config/setups.yaml eligibility.min_trades); crypto keeps the harness's own precedent.
KILL_CRITERIA_BY_MARKET = {
    "nse": replace(DEFAULT_KILL_CRITERIA, min_sample_size=500),
    "bse": replace(DEFAULT_KILL_CRITERIA, min_sample_size=500),
    "crypto": DEFAULT_KILL_CRITERIA,
}


class BaseDrifted(Exception):
    pass


def market_bundle(market: str, settings: Any) -> Market:
    return {"crypto": crypto_market, "bse": bse_market}.get(market, nse_market)(settings)


def load_real_daily_frames(
    db: Path, *, max_codes: int = 100, exclude: frozenset[str] | set[str] = frozenset()
) -> tuple[dict[str, Any], dict[str, str]]:
    """Real daily history for the `max_codes` most liquid instruments (mean close x volume
    over the last 60 sessions) with enough history, `daily_features`-enriched - the same
    source and enrichment `scripts/entry_search.py` already reads. Ranked by liquidity
    rather than taking the first N codes alphabetically, which on crypto would pick
    whichever pairs happen to sort first rather than the ones anyone actually trades."""

    raws: dict[str, Any] = {}
    turnover: dict[str, float] = {}
    with CandleStore(db) as store:
        for code in store.codes(Interval.D1):
            if code in exclude:
                continue
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            tail = raw.iloc[-60:]
            turnover[code] = float((tail["close"] * tail["volume"]).mean())
            raws[code] = raw
    ranked = sorted(raws, key=lambda c: turnover[c], reverse=True)[:max_codes]
    frames = {code: daily_features(raws[code]) for code in ranked}
    return frames, {code: code for code in ranked}


def candidate_verdict(
    report: GauntletReport,
    beats_random_margin: bool,
    *,
    baseline_net_r: float | None = None,
    max_null_p_value: float | None = None,
) -> tuple[bool, list[str]]:
    """The one pass/fail definition for a lab candidate: full gauntlet, kill criteria, the
    +0.10R-over-random margin every live setup's eligibility() requires, and - for a
    replacement - a random-benchmark p-value tightened for how many candidates the search
    has tried (multiple-testing guard) and a net expectancy that beats the retired setup's
    own measured result. Returns (passed, reasons it failed)."""

    reasons: list[str] = []
    if report.stopped_at is not None:
        reasons.append(f"stopped at {report.stopped_at}")
    elif not bool((report.kill_criteria or {}).get("passed")):
        reasons.append("kill criteria not met")
    if not beats_random_margin:
        reasons.append(f"not +{MUST_BEAT_RANDOM_BY_R}R over random timing")
    bench = report.stages.get("random_entry_benchmark") or {}
    p_value = bench.get("p_value")
    if max_null_p_value is not None and (p_value is None or p_value > max_null_p_value):
        reasons.append(f"random-benchmark p={p_value} > search-adjusted {max_null_p_value:.4f}")
    net_r = (report.stages.get("in_sample") or {}).get("net_expectancy_r")
    if baseline_net_r is not None and net_r is not None and net_r <= baseline_net_r:
        reasons.append(
            f"net {net_r:+.3f}R does not beat the retired setup's {baseline_net_r:+.3f}R"
        )
    return not reasons, reasons


def production_source_for(candidate: Any, setup_kind_name: str) -> str | None:
    generator = getattr(candidate, "production_source", None)
    if generator is not None:
        return str(generator(setup_kind_name))
    template = PRODUCTION_SOURCE_GENERATORS.get(candidate.name)
    return template(setup_kind_name) if template is not None else None


def already_authored_today(
    candidate_name: str,
    market: str,
    *,
    registry_path: Path = OUTPUT / "registry.sqlite",
    as_of: str | None = None,
) -> bool:
    """True if `author_and_validate` already recorded a run for this exact
    (candidate, market) pair today. The full harness gauntlet (null baseline, walk-forward,
    sensitivity, Monte Carlo, regime split) is expensive; re-running it every single day a
    SUSTAINED failure persists would be wasteful when nothing about the candidate's own
    validation plausibly changes day to day. Checked against the Registry's own experiment
    metadata (already recorded by every `author_and_validate` call) rather than a separate
    tracking file. `started_at` is UTC (`Registry.now()`); `as_of` is compared as a plain
    date-string prefix, so this is date-based, not session-based - a small, disclosed
    imprecision near the UTC/IST midnight boundary, not a correctness-critical dedupe like
    `rolling_failure_monitor`'s (this only ever skips redundant compute, never changes what
    counts as a failure)."""

    if not registry_path.exists():
        return False
    as_of = as_of or datetime.now(IST).date().isoformat()
    with Registry(registry_path, readonly=True) as registry:
        for run in registry.runs(limit=200):
            meta = run.get("metadata") or {}
            if meta.get("candidate") != candidate_name or meta.get("market") != market:
                continue
            started = run.get("started_at") or ""
            if started[:10] == as_of:
                return True
    return False


def author_and_validate(
    candidate: LabSetup,
    market: str,
    *,
    root: Path = ROOT,
    db: Path = ROOT / "data" / "tradedesk.duckdb",
    registry_path: Path = OUTPUT / "registry.sqlite",
    equity: float = 1_000_000.0,
    risk_pct: float = 0.005,
    max_codes: int = 100,
    kill_criteria: KillCriteria | None = None,
    replacement_for: SetupKind | None = None,
    family: str | None = None,
    frames: tuple[dict[str, Any], dict[str, str]] | None = None,
    baseline_net_r: float | None = None,
    max_null_p_value: float | None = None,
    skip_base_check: bool = False,
    output_dir: Path = OUTPUT,
) -> tuple[GauntletReport, bool, str, str, Path]:
    """Returns (gauntlet_report, beats_random_margin, experiment_id, candidate_id,
    artifact_path) - the artifact path is returned rather than left for a caller to
    reconstruct from `OUTPUT`/`experiment_id` itself, since only this function actually
    knows where it wrote the evidence file. Raises `BaseDrifted` if `verify_base()` reports
    any change to a production file - see module docstring.

    `replacement_for`, when given, registers the candidate under the exact
    `<market>:<setup>:replacement` family `retire_replace.py::find_lab_replacement` already
    looks up - the integration point that module's own docstring flagged as future work
    ("it will pick up a genuine replacement the moment something ... starts registering
    candidates under that family/metric convention"). Without it, the candidate is recorded
    under `<market>:<candidate.name>:replacement` instead - still real, auditable, just not
    linked to any one specific failing setup (standalone/manual authoring). `family`
    overrides both (the replacement search registers everything it tries for a market under
    `<market>:replacement`).

    Costs, sizing step and the kill-criteria sample floor all come from the market being
    searched (crypto: CoinDCX fees/TDS and fractional quantities) - before 2026-09-29 every
    candidate was costed as an NSE equity trade in whole units, which is wrong for crypto
    and made any coin priced above the per-trade risk budget unsizeable.

    `frames` lets a caller testing many candidates load the market's history once;
    `skip_base_check` is only for such a caller that has already run `verify_base()` for
    this batch (it is not a way around the check)."""

    if not skip_base_check:
        base_state = verify_base()
        if not base_state["unchanged"]:
            raise BaseDrifted(
                "production has drifted from the recorded base manifest - refusing to author "
                f"a candidate until this is reviewed: {base_state['changed']}"
            )

    settings = load_config(root)
    mkt = market_bundle(market, settings)
    if frames is None:
        frames = load_real_daily_frames(
            db, max_codes=max_codes, exclude=frozenset(mkt.universe_rules.exclude_codes)
        )
    frame_map, symbols = frames
    kill_criteria = kill_criteria or KILL_CRITERIA_BY_MARKET.get(market, DEFAULT_KILL_CRITERIA)
    if family is None:
        family = (
            f"{market}:{replacement_for.value}:replacement"
            if replacement_for is not None
            else f"{market}:{candidate.name}:replacement"
        )

    with Registry(registry_path) as registry:
        experiment_id = registry.begin(
            {"candidate": candidate.name, "market": market, "max_codes": max_codes}
        )
        try:
            trades = simulate_single_setup(
                candidate,
                frame_map,
                symbols,
                costs_model=mkt.costs,
                equity=equity,
                risk_pct=risk_pct,
                qty_step=mkt.qty_step,
            )
            harness_trades, bars_by_code = to_harness_trades(trades, frame_map)
            report = run_gauntlet(
                f"lab_candidate:{candidate.name}:{market}",
                harness_trades,
                bars_by_code,
                kill_criteria,
            )
            beats_random_margin = False
            stage = report.stages.get("random_entry_benchmark")
            if stage is not None:
                setup_mean = stage.get("setup_mean_gross_r")
                null_mean = stage.get("null_mean_gross_r")
                if setup_mean is not None and null_mean is not None:
                    beats_random_margin = (setup_mean - null_mean) >= MUST_BEAT_RANDOM_BY_R
            passed, fail_reasons = candidate_verdict(
                report,
                beats_random_margin,
                baseline_net_r=baseline_net_r,
                max_null_p_value=max_null_p_value,
            )
            # Mean net R over every realised trade, reported even when the gauntlet stopped
            # before its in-sample stage - the search ranks near-misses by it.
            net_r = (
                (report.stages.get("in_sample") or {}).get("net_expectancy_r")
                if report.stages.get("in_sample")
                else (sum(t.net_r for t in harness_trades) / len(harness_trades))
                if harness_trades
                else None
            )
            win_rate = (
                sum(1 for t in harness_trades if t.net_r > 0) / len(harness_trades)
                if harness_trades
                else None
            )
            artifact_dir = output_dir / "candidates" / experiment_id
            artifact_path = artifact_dir / "report.json"
            write_json(
                artifact_path,
                {
                    "strategy_name": report.strategy_name,
                    "description": getattr(candidate, "describe", lambda: candidate.name)(),
                    "stopped_at": report.stopped_at,
                    "stages": report.stages,
                    "kill_criteria": report.kill_criteria,
                    "beats_random_margin": beats_random_margin,
                    "passed": passed,
                    "fail_reasons": fail_reasons,
                    "net_r": net_r,
                    "win_rate": win_rate,
                    "baseline_net_r": baseline_net_r,
                    "max_null_p_value": max_null_p_value,
                    "n_trades": len(harness_trades),
                },
            )
            if passed:
                # Written ONLY on a pass, so a human reviewing the proposal sees exactly
                # the file apply() will later copy verbatim into src/tradedesk/setups/ -
                # nothing is generated for a candidate that never clears the gauntlet.
                source = production_source_for(candidate, candidate.name.upper())
                if source is not None:
                    (artifact_dir / "production_setup.py").write_text(source, encoding="utf-8")
            candidate_id = registry.candidate(
                experiment_id,
                family,
                {
                    "candidate": candidate.name,
                    "market": market,
                    "max_codes": max_codes,
                    "risk_pct": risk_pct,
                },
                {
                    "net_r": net_r,
                    "win_rate": win_rate,
                    "passed_gauntlet": passed,
                    "beats_random_margin": beats_random_margin,
                    "n_trades": len(harness_trades),
                    "stopped_at": report.stopped_at,
                    "p_value": (stage or {}).get("p_value"),
                    "fail_reasons": fail_reasons,
                },
                status="completed" if passed else "rejected",
                artifact=str(artifact_path),
                sha=digest(artifact_path),
            )
            registry.finish(experiment_id, report={"passed": passed})
        except Exception as exc:  # pragma: no cover - defensive bookkeeping only
            registry.finish(experiment_id, error=str(exc))
            raise

    return report, beats_random_margin, experiment_id, candidate_id, artifact_path


def propose_new_detector(
    candidate: LabSetup,
    market: str,
    report: GauntletReport,
    beats_random_margin: bool,
    experiment_id: str,
    candidate_id: str,
    *,
    baseline_net_r: float | None = None,
    max_null_p_value: float | None = None,
) -> NewDetectorCode | None:
    """Only builds a proposal payload if the run actually passed (`candidate_verdict`, with
    the same baseline/p-value bar the run was judged against). Target files are the exact
    five production wiring points identified in the approved plan; `apply.py` refuses to
    write anywhere else regardless."""

    passed, _ = candidate_verdict(
        report,
        beats_random_margin,
        baseline_net_r=baseline_net_r,
        max_null_p_value=max_null_p_value,
    )
    if not passed:
        return None
    return detector_payload(candidate, market, experiment_id, candidate_id)


def detector_payload(
    candidate: Any, market: str, experiment_id: str, candidate_id: str
) -> NewDetectorCode:
    """The NEW_DETECTOR payload for a candidate a caller has ALREADY judged passed (via
    `candidate_verdict`) - `propose_new_detector` is the checked entry point."""
    return NewDetectorCode(
        setup_kind=candidate.name,
        market=market,
        lab_module_path=(
            "tradedesk_lab/candidates/rules.py"
            if hasattr(candidate, "production_source")
            else f"tradedesk_lab/candidates/{candidate.name}.py"
        ),
        lab_experiment_id=experiment_id,
        lab_candidate_id=candidate_id,
        target_files=[
            "src/tradedesk/engine/patterns.py",
            f"src/tradedesk/setups/{candidate.name}.py",
            "src/tradedesk/engine/signals.py",
            "src/tradedesk/setups/__init__.py",
            "config/setups.yaml",
        ],
    )
