"""Using the model (PLAN.md 10.5): shadow logging, grade/size effects that only ever
lower, and the calibration drift monitor.

- Shadow mode: the probability is logged on every signal and changes nothing.
- Switch-on: only when `ml.enabled` and not `ml.shadow`, and then a probability can
  only lower a grade (A needs >= grade_a_min_probability, < grade_c_below_probability
  drops to C) or halve the size (< half_size_below_probability). It can never raise a
  grade or a size above the hard caps in risk.yaml - `apply_probability` is pure and
  tested for that.
- Drift: among live signals scored near p, about p should succeed. When a well-populated
  bucket drifts beyond tolerance, the layer is paused (the setups are not).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.backtest.runner import MarketData
from tradedesk.broker.indstocks.models import IST
from tradedesk.config.models import MlConfig
from tradedesk.engine.scoring import Grade
from tradedesk.prediction.calibration import DriftReport, drift_check
from tradedesk.prediction.features import FEATURE_NAMES, signal_features
from tradedesk.prediction.train import ModelBundle, market_context
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry

__all__ = [
    "DriftReport",
    "apply_probability",
    "drift_check",
    "is_current",
    "latest_bundle",
    "latest_bundles_by_setup",
    "log_shadow",
    "probability",
    "read_shadow",
    "score_watchlist",
]


def probability(bundle: ModelBundle, features: dict[str, float]) -> float:
    """Refuses to score a bundle trained against a different FEATURE_NAMES/FEATURE_VERSION
    (e.g. a v2 bundle after the v3 sector_return_1d/5d change, 2026-09-13) rather than
    silently building a DataFrame with mismatched columns - a stale bundle's `.features`
    list would produce garbage predictions with no error otherwise."""
    from tradedesk.prediction.features import FEATURE_VERSION

    if bundle.feature_version is not None and bundle.feature_version != FEATURE_VERSION:
        raise ValueError(
            f"model {bundle.version} was trained on feature_version="
            f"{bundle.feature_version!r}, current is {FEATURE_VERSION!r} - retrain before "
            "scoring with this bundle"
        )
    X = pd.DataFrame([features], columns=FEATURE_NAMES).astype(float)
    return float(bundle.predict_proba(X)[0])


def is_current(bundle: ModelBundle) -> bool:
    """True when `bundle` was trained against the CURRENT FEATURE_VERSION.

    `probability()` deliberately RAISES on a stale bundle - scoring v3 weights against v4
    columns would be silently wrong, which is worse than an error. But that strictness must
    never reach the evening scan: the prediction layer is advisory (shadow mode only ever
    appends a note), so a stale model has to degrade to "no ML opinion", not take the
    watchlist build down with it. Bumping FEATURE_VERSION without retraining did exactly
    that on 2026-09-13 - `tradedesk scan` would have crashed on the next `tradedesk-after-
    close` run, leaving the following morning's live session with no watchlist at all.
    Callers select with this; `probability()` keeps the hard guard as the backstop."""
    from tradedesk.prediction.features import FEATURE_VERSION

    return bundle.feature_version is None or bundle.feature_version == FEATURE_VERSION


def apply_probability(e: WatchlistEntry, p: float, cfg: MlConfig) -> WatchlistEntry:
    """Return a copy of the entry with the model's effect applied. Never raises a grade,
    never increases qty; in shadow mode only the note is added."""
    note = f"model p(T1 before stop) = {p:.2f}"
    update: dict[str, Any] = {"score_notes": [*e.score_notes, note], "probability": p}
    if not cfg.enabled or cfg.shadow:
        return e.model_copy(update=update)
    grade = e.grade
    if p < float(cfg.grade_c_below_probability):
        grade = Grade.C
    elif grade is Grade.A and p < float(cfg.grade_a_min_probability):
        grade = Grade.B
    qty = e.qty
    if p < float(cfg.half_size_below_probability):
        qty = e.qty // 2
    if grade is not e.grade:
        update["grade"] = grade
        update["alertable"] = e.alertable and grade is not Grade.C
    if qty != e.qty:
        scale = qty / e.qty if e.qty else 0.0
        update.update(
            {
                "qty": qty,
                "risk_amount": e.risk_amount * scale,
                "risk_pct": e.risk_pct * scale,
                "position_value": e.position_value * scale,
                "size_caps": [*e.size_caps, f"model p {p:.2f}: half size"],
            }
        )
    return e.model_copy(update=update)


# ------------------------------------------------------------- shadow log


def log_shadow(path: Path, signal_id: str, p: float, version: str, **extra: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "at": datetime.now(IST).isoformat(),
                    "signal_id": signal_id,
                    "p": round(p, 4),
                    "version": version,
                    **extra,
                },
                default=str,
            )
            + "\n"
        )


def read_shadow(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["at", "signal_id", "p", "version"])
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return pd.DataFrame(rows)


def latest_bundle(folder: Path) -> ModelBundle | None:
    """Newest single POOLED bundle - a plain "<timestamp>.joblib" with no setup suffix.
    Per-setup bundles (see `latest_bundles_by_setup`) are named "<timestamp>-<setup>.joblib"
    and are skipped here via the glob, so a pooled-model call site never accidentally picks
    up one setup's specialised model."""
    files = sorted(f for f in folder.glob("*.joblib") if not _is_per_setup_bundle(f))
    return ModelBundle.load(files[-1]) if files else None


def _is_per_setup_bundle(path: Path) -> bool:
    from tradedesk.engine.signals import SetupKind

    stem = path.stem
    return any(stem.endswith(f"-{k.value}") for k in SetupKind)


def latest_bundles_by_setup(folder: Path) -> dict[str, ModelBundle]:
    """Newest "<timestamp>-<setup>.joblib" bundle per setup (see
    `train.py::train_per_setup`) - one independently-tuned model per setup instead of one
    pooled across all of them. A setup with no saved bundle (e.g. it had fewer than
    `min_rows` resolved signals) is simply absent from the returned dict; callers must
    treat a missing setup as "no ML opinion for this setup" (see `score_watchlist`'s
    handling below), never as a reason to fall back to a different setup's model - a
    base_breakout signal must never be scored by a trend_pullback-tuned model."""
    from tradedesk.engine.signals import SetupKind

    out: dict[str, ModelBundle] = {}
    for kind in SetupKind:
        files = sorted(folder.glob(f"*-{kind.value}.joblib"))
        if files:
            out[kind.value] = ModelBundle.load(files[-1])
    return out


def score_watchlist(
    bundle: ModelBundle | dict[str, ModelBundle],
    wl: Watchlist,
    md: MarketData,
    cfg: MlConfig,
    *,
    shadow_log: Path | None = None,
) -> tuple[Watchlist, dict[str, float]]:
    """Score every entry with computable features - including rejected ones (2026-09-15;
    was `on_watchlist` entries only until then). Shadow (default): note + log only. Enabled:
    `apply_probability` (which can only lower). Returns the new watchlist and the
    probabilities by symbol.

    Why rejected entries are scored too: a rejected entry can never become alertable from
    this (shadow mode only ever adds a note; enabled mode can only lower a grade/size, and a
    rejected entry is already excluded from alerting by `rejected_for`, untouched here) - so
    scoring it is purely informational, for a human comparing "if I were to consider this
    one anyway, what does the model say" across every call on the sheet, not just the ones
    that already cleared every other gate. `pos is None` (no feature data for that code on
    this date) is still skipped - nothing to score without.

    `bundle` accepts either a single pooled `ModelBundle` (existing behaviour, unchanged)
    or a `dict[setup_value, ModelBundle]` from `latest_bundles_by_setup()` (2026-09-13):
    each entry is then scored by ITS OWN setup's model instead of one model averaged
    across all setups - see `train_per_setup()`'s docstring for why that dilutes signal.
    An entry whose setup has no dedicated bundle in the dict is left completely unscored
    (no note added, `probs` has no entry for it) rather than silently reused with a
    different setup's model or the pooled one - "no model for this setup" must be visibly
    different from "the model said no", not papered over."""
    probs: dict[str, float] = {}
    entries: list[WatchlistEntry] = []
    for e in wl.entries:
        code = e.signal.scrip_code
        pos = md.pos_by_date.get(code, {}).get(wl.on)
        if pos is None:
            entries.append(e)
            continue
        b = bundle.get(e.signal.setup.value) if isinstance(bundle, dict) else bundle
        if b is None:
            entries.append(e)
            continue
        feats = md.features[code].iloc[: pos + 1]
        f = signal_features(e.signal, feats, **market_context(md, code, wl.on))
        p = probability(b, f)
        probs[e.signal.symbol] = p
        if shadow_log is not None:
            log_shadow(
                shadow_log,
                e.signal.id,
                p,
                b.version,
                symbol=e.signal.symbol,
                grade=e.grade.value,
                on=wl.on,
            )
        entries.append(apply_probability(e, p, cfg))
    return wl.model_copy(update={"entries": entries}), probs
