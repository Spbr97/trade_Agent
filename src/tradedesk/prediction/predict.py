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
    "latest_bundle",
    "log_shadow",
    "probability",
    "read_shadow",
    "score_watchlist",
]


def probability(bundle: ModelBundle, features: dict[str, float]) -> float:
    X = pd.DataFrame([features], columns=FEATURE_NAMES).astype(float)
    return float(bundle.predict_proba(X)[0])


def apply_probability(e: WatchlistEntry, p: float, cfg: MlConfig) -> WatchlistEntry:
    """Return a copy of the entry with the model's effect applied. Never raises a grade,
    never increases qty; in shadow mode only the note is added."""
    note = f"model p(T1 before stop) = {p:.2f}"
    update: dict[str, Any] = {"score_notes": [*e.score_notes, note]}
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
    files = sorted(folder.glob("*.joblib"))
    return ModelBundle.load(files[-1]) if files else None


def score_watchlist(
    bundle: ModelBundle,
    wl: Watchlist,
    md: MarketData,
    cfg: MlConfig,
    *,
    shadow_log: Path | None = None,
) -> tuple[Watchlist, dict[str, float]]:
    """Score every active entry with the model. Shadow (default): note + log only.
    Enabled: `apply_probability` (which can only lower). Returns the new watchlist and
    the probabilities by symbol."""
    probs: dict[str, float] = {}
    entries: list[WatchlistEntry] = []
    for e in wl.entries:
        code = e.signal.scrip_code
        pos = md.pos_by_date.get(code, {}).get(wl.on)
        if not e.on_watchlist or pos is None:
            entries.append(e)
            continue
        feats = md.features[code].iloc[: pos + 1]
        f = signal_features(e.signal, feats, **market_context(md, code, wl.on))
        p = probability(bundle, f)
        probs[e.signal.symbol] = p
        if shadow_log is not None:
            log_shadow(
                shadow_log,
                e.signal.id,
                p,
                bundle.version,
                symbol=e.signal.symbol,
                grade=e.grade.value,
                on=wl.on,
            )
        entries.append(apply_probability(e, p, cfg))
    return wl.model_copy(update={"entries": entries}), probs
