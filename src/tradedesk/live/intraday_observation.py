"""Step 7 of the intraday plan: run scan_bar end-to-end against real market data with NO
alert path wired in at all, so the full pipeline - data, features, MTF alignment, regime,
scan_bar, and the Phase 1 eligibility gate - can be proven correct before any setup is
trusted to alert on anything.

This module has no import of, or reference to, anything alert-shaped (alerts/router.py,
Telegram, desktop notifications). That is deliberate and load-bearing: "cannot alert" is
enforced by the dependency graph, not by a runtime flag someone could flip later. A test
(`test_intraday_observation.py::test_module_has_no_alert_capability`) checks this directly
against the module's own source, not just its behaviour on one input.

This is a REPLAY over already-fetched, already-verified stored bars (scripts/
backfill_intraday.py, verified 2026-09-13), not a live WebSocket session - swapping in a real
tick feed later is a separate integration and does not touch this module, per CLAUDE.md's
hard rule that the live scanner and the backtester/replay share one signal-generation path
(`scan_bar` is called identically either way; only the bar source differs).

Eligibility is evaluated ONCE PER SETUP from measured population statistics (the same
setup-level granularity `journal/stats.py::track_record` already uses for daily setups, not
per individual signal) - a setup that has not cleared the bar produces signals that are all,
uniformly, NO TRADE with reasons; one that has would have every signal marked eligible. The
module does not care which case it is in; it reports honestly either way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.engine.intraday_engine import IntradaySnapshot, scan_bar
from tradedesk.engine.intraday_regime import IntradayRegime, classify_intraday_regime
from tradedesk.engine.intraday_signals import IntradaySetupKind, IntradaySignal
from tradedesk.engine.mtf import HIERARCHY, align
from tradedesk.engine.scoring import EligibilityPolicy, eligibility


@dataclass(frozen=True)
class SetupEvidence:
    """Measured population statistics for one intraday setup, at whatever granularity they
    were measured (here: `scripts/validate_intraday_setup.py`'s null-baseline run). The
    caller supplies this - this module does not compute it - so the observation session can
    be re-run cheaply against updated evidence without re-deriving it here."""

    trades: int
    oos_trades: int
    win_rate: float
    expectancy_r: float
    random_baseline_r: float | None


@dataclass(frozen=True)
class ObservedSignal:
    signal: IntradaySignal
    eligible: bool
    reasons: tuple[str, ...]


@dataclass
class ObservationSummary:
    bars_scanned: int = 0
    signals: list[ObservedSignal] = field(default_factory=list)

    @property
    def would_have_alerted(self) -> int:
        return sum(1 for s in self.signals if s.eligible)

    def text(self) -> str:
        lines = [
            f"observation run: {self.bars_scanned} bar-closes scanned, "
            f"{len(self.signals)} signal(s) armed, {self.would_have_alerted} eligible "
            "(none of this ever reached an alert - this module cannot call one)"
        ]
        for o in self.signals:
            tag = "ELIGIBLE - would alert" if o.eligible else "NO TRADE"
            lines.append(
                f"  [{tag}] {o.signal.armed_at} {o.signal.scrip_code} {o.signal.setup.value} "
                f"trigger={o.signal.trigger:.2f} stop={o.signal.stop:.2f}"
            )
            if not o.eligible:
                for r in o.reasons:
                    lines.append(f"      - {r}")
        return "\n".join(lines)


def observe(
    features_by_interval: Mapping[Interval, Mapping[str, pd.DataFrame]],
    codes: Sequence[str],
    arming_interval: Interval,
    setups: Sequence[IntradaySetupKind],
    params: Mapping[str, Mapping[str, Any]],
    evidence: Mapping[IntradaySetupKind, SetupEvidence],
    *,
    policy: EligibilityPolicy | None = None,
) -> ObservationSummary:
    """Walk every bar-close of `arming_interval` present for ANY code in
    `features_by_interval`, calling `scan_bar` exactly as a live session would, and evaluate
    each armed signal's setup against `evidence` via the same `eligibility()` Phase 1 uses.

    `features_by_interval` values are `engine/indicators.py::intraday_features` frames,
    ascending, already covering the full window to observe - this function does not fetch
    anything."""
    pol = policy or EligibilityPolicy()
    arming_frames = features_by_interval.get(arming_interval, {})
    all_closes: set[pd.Timestamp] = set()
    for code in codes:
        f = arming_frames.get(code)
        if f is not None and len(f):
            all_closes.update(
                pd.DatetimeIndex(f.index) + pd.Timedelta(seconds=arming_interval.seconds)
            )
    ordered_closes = sorted(all_closes)

    # Eligibility is a property of the SETUP (measured population stats), not of any one
    # signal - computed once per setup, reused for every signal that setup produces.
    setup_verdicts: dict[IntradaySetupKind, tuple[bool, tuple[str, ...]]] = {}
    for kind in setups:
        ev = evidence.get(kind)
        if ev is None:
            setup_verdicts[kind] = (
                False,
                (f"no measured evidence supplied for {kind.value}",),
            )
            continue
        setup_verdicts[kind] = eligibility(
            trades=ev.trades, oos_trades=ev.oos_trades, win_rate=ev.win_rate,
            expectancy_r=ev.expectancy_r, random_baseline_r=ev.random_baseline_r, policy=pol,
        )  # fmt: skip

    summary = ObservationSummary()
    regime_cache: dict[str, pd.Series] = {}
    for code in codes:
        f = arming_frames.get(code)
        if f is not None and len(f):
            regime_cache[code] = classify_intraday_regime(f)

    for at in ordered_closes:
        summary.bars_scanned += 1
        alignment = {}
        regime: dict[str, IntradayRegime] = {}
        for code in codes:
            code_frames = {
                iv: features_by_interval.get(iv, {}).get(code, pd.DataFrame()) for iv in HIERARCHY
            }
            alignment[code] = align(code_frames, at)
            reg_series = regime_cache.get(code)
            if reg_series is not None and not reg_series.empty:
                eligible_idx = reg_series.index[
                    (pd.DatetimeIndex(reg_series.index) + pd.Timedelta(seconds=arming_interval.seconds)) <= at  # noqa: E501
                ]
                if len(eligible_idx):
                    regime[code] = reg_series.loc[eligible_idx[-1]]

        snapshot = IntradaySnapshot(
            at=at, arming_interval=arming_interval, features=arming_frames,
            symbols={c: c for c in codes}, alignment=alignment, regime=regime, universe=list(codes),  # noqa: E501
        )  # fmt: skip
        for sig in scan_bar(snapshot, setups, params):
            ok, reasons = setup_verdicts.get(sig.setup, (False, ("unregistered setup",)))
            summary.signals.append(ObservedSignal(signal=sig, eligible=ok, reasons=reasons))

    return summary
