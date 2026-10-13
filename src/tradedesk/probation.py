"""Probation desk: a small, capped, self-terminating tier for detectors that passed the
backtest gauntlet but have no forward record yet.

Why it exists (explicit user decision, 2026-10-13): nothing had alerted for ~1.5 months because
the full eligibility bar needs hundreds of forward trades. Probation lets a human-approved
(setup, market) pair send real, clearly labelled calls at the first-50-trades risk size while its
forward record builds. It is held to the user's own standard - if the money lost exceeds the money
made, it ends:

* every call is sized at the risk.yaml per-trade risk (0.25% of the trading pot), whole shares,
  inside the position-value cap, at most `max_new_calls_per_day` a day;
* a setup is suspended when its cumulative net R after costs falls to `setup_floor_r`, the whole
  desk when it falls to `desk_floor_r`, and the desk at `checkpoint_calls` resolved calls if the
  net rupee result is not positive;
* a suspension is written to disk and never lifts itself - only a human edit does.

It does not touch engine/scoring.py::eligibility(), the live monitor, or any order path: the human
places every order. Calls are read from the per-market signal tracker's log, so they use exactly
the signals the shadow tracker already grades.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from tradedesk.broker.indstocks.models import IST
from tradedesk.models import TradeType

ROOT = Path(__file__).resolve().parent.parent.parent
POLICY_PATH = ROOT / "config" / "probation.yaml"
STATE_DIR = ROOT / "data" / "m14_m18" / "probation"
GRADE_ORDER = {"A+": 0, "A": 1, "B": 2, "C": 3}
# Eligibility-evidence reasons are exactly what probation replaces; any other rejection
# (regime, net reward:risk, sizing, benched, retired) still blocks a call.
EVIDENCE_PREFIXES = (
    "only ",
    "win rate",
    "win-rate",
    "expectancy",
    "never measured",
    "beats random",
)


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    started_on: date = date(2999, 1, 1)
    approvals: tuple[tuple[str, str], ...] = ()
    max_new_calls_per_day: int = 2
    worst_allowed_grade: str = "B"
    setup_floor_r: float = -3.0
    desk_floor_r: float = -5.0
    checkpoint_calls: int = 15
    min_mean_net_r_after_checkpoint: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def load_policy(path: Path = POLICY_PATH) -> Policy:
    if not path.exists():
        return Policy()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    block = raw.get("probation") or {}
    approvals = tuple(
        (str(a["setup"]), str(a["market"])) for a in (block.get("approvals") or [])
    )
    started = block.get("started_on")
    return Policy(
        enabled=bool(block.get("enabled", False)),
        started_on=started if isinstance(started, date) else date.fromisoformat(str(started)),
        approvals=approvals,
        max_new_calls_per_day=int(block.get("max_new_calls_per_day", 2)),
        worst_allowed_grade=str(block.get("worst_allowed_grade", "B")),
        setup_floor_r=float(block.get("setup_floor_r", -3.0)),
        desk_floor_r=float(block.get("desk_floor_r", -5.0)),
        checkpoint_calls=int(block.get("checkpoint_calls", 15)),
        min_mean_net_r_after_checkpoint=float(block.get("min_mean_net_r_after_checkpoint", 0.0)),
    )


def evidence_only_rejections(reasons: list[str]) -> bool:
    return all(r.lower().startswith(EVIDENCE_PREFIXES) for r in reasons)


def size_call(
    entry: float, stop: float, capital: float, risk_pct: float, max_value_pct: float
) -> int:
    """Whole shares at the per-trade risk, never above the position-value cap."""

    per_share = entry - stop
    if per_share <= 0 or entry <= 0:
        return 0
    by_risk = int((capital * risk_pct) // per_share)
    by_value = int((capital * max_value_pct) // entry)
    return max(0, min(by_risk, by_value))


def net_result(
    cost_model: Any, qty: int, entry: float, stop: float, exit_price: float
) -> tuple[float, float]:
    """(net rupees, net R) of a resolved delivery trade after the market's real costs."""

    pnl = cost_model.net_pnl(
        trade_type=TradeType.DELIVERY,
        qty=float(qty),
        entry_price=Decimal(str(round(entry, 2))),
        exit_price=Decimal(str(round(exit_price, 2))),
    )
    rupees = float(pnl)
    return rupees, rupees / (qty * (entry - stop))


def evaluate_suspensions(
    resolved: list[dict[str, Any]], policy: Policy
) -> tuple[dict[str, str], str | None]:
    """(per-setup suspension reasons, desk suspension reason) from the resolved ledger."""

    by_setup: dict[str, float] = {}
    for call in resolved:
        by_setup[call["setup"]] = by_setup.get(call["setup"], 0.0) + call["net_r"]
    setups = {
        s: f"cumulative net R {r:+.2f} at or below the {policy.setup_floor_r:+.1f}R floor"
        for s, r in by_setup.items()
        if r <= policy.setup_floor_r
    }
    total_r = sum(c["net_r"] for c in resolved)
    total_rupees = sum(c["net_rupees"] for c in resolved)
    desk: str | None = None
    if total_r <= policy.desk_floor_r:
        desk = f"desk net R {total_r:+.2f} at or below the {policy.desk_floor_r:+.1f}R floor"
    elif len(resolved) >= policy.checkpoint_calls:
        mean_r = total_r / len(resolved)
        if total_rupees <= 0 or mean_r < policy.min_mean_net_r_after_checkpoint:
            desk = (
                f"after {len(resolved)} resolved calls the net result is Rs {total_rupees:,.0f} "
                f"({mean_r:+.3f}R per call): money lost is not below money made"
            )
    return setups, desk


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def run_probation(
    market: str,
    *,
    rows: list[dict[str, Any]],
    cost_model: Any,
    capital: float,
    risk_pct: float,
    max_value_pct: float,
    latest_session: date,
    policy: Policy | None = None,
    state_dir: Path = STATE_DIR,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure core: signal-tracker rows in, calls and desk status out (state kept in state_dir)."""

    policy = policy or load_policy()
    now = now or datetime.now(IST)
    root = state_dir / market
    status: dict[str, Any] = {
        "market": market,
        "authority": "probation_capped_human_places_orders",
        "generated_at": now.isoformat(),
        "enabled": policy.enabled,
        "started_on": policy.started_on.isoformat(),
    }
    if not policy.enabled:
        status.update(state="disabled", calls_today=[], ledger={})
        return status
    approved = {s for s, m in policy.approvals if m == market}
    calls_path = root / "calls.jsonl"
    stored = {c["signal_id"]: c for c in _read_jsonl(calls_path)}
    worst = GRADE_ORDER[policy.worst_allowed_grade]
    ledger_fresh = [
        r
        for r in rows
        if r["setup"] in approved
        and r.get("source") == "live"
        and r["armed_on"] >= policy.started_on.isoformat()
    ]
    # 1) record each new qualifying call once, before anything about its outcome is known
    issued_today = sum(1 for c in stored.values() if c["issued_on"] == latest_session.isoformat())
    suspended_path = root / "suspended.json"
    suspended: dict[str, Any] = (
        json.loads(suspended_path.read_text(encoding="utf-8")) if suspended_path.exists() else {}
    )
    new_calls: list[dict[str, Any]] = []
    for r in sorted(ledger_fresh, key=lambda x: GRADE_ORDER.get(x["grade"], 9)):
        if r["signal_id"] in stored or r["armed_on"] != latest_session.isoformat():
            continue
        if suspended.get("desk") or r["setup"] in suspended.get("setups", {}):
            continue
        if GRADE_ORDER.get(r["grade"], 9) > worst:
            continue
        if not evidence_only_rejections(r["rejected_for"]):
            continue
        if issued_today + len(new_calls) >= policy.max_new_calls_per_day:
            break
        qty = size_call(r["entry"], r["stop"], capital, risk_pct, max_value_pct)
        if qty < 1:
            continue
        new_calls.append(
            {
                "signal_id": r["signal_id"],
                "setup": r["setup"],
                "symbol": r["symbol"],
                "scrip_code": r["scrip_code"],
                "grade": r["grade"],
                "issued_on": latest_session.isoformat(),
                "issued_at": now.isoformat(),
                "entry": r["entry"],
                "stop": r["stop"],
                "t1": r["t1"],
                "t2": r["t2"],
                "qty": qty,
                "risk_rupees": round(qty * (r["entry"] - r["stop"]), 2),
                "label": "PROBATION - unproven detector, small size, you place the order",
            }
        )
    if new_calls:
        root.mkdir(parents=True, exist_ok=True)
        with calls_path.open("a", encoding="utf-8") as handle:
            for c in new_calls:
                handle.write(json.dumps(c, sort_keys=True) + "\n")
                stored[c["signal_id"]] = c
    # 2) resolve whatever the tracker has graded since, after real costs
    by_id = {r["signal_id"]: r for r in rows}
    resolved: list[dict[str, Any]] = []
    open_calls: list[dict[str, Any]] = []
    for call in stored.values():
        tracked = by_id.get(call["signal_id"])
        if tracked and tracked.get("exit_price") is not None and tracked.get("outcome"):
            rupees, net_r = net_result(
                cost_model, call["qty"], call["entry"], call["stop"], float(tracked["exit_price"])
            )
            resolved.append(
                {**call, "outcome": tracked["outcome"], "exit_price": tracked["exit_price"],
                 "net_rupees": round(rupees, 2), "net_r": round(net_r, 4)}
            )  # fmt: skip
        else:
            open_calls.append(call)
    # 3) suspensions are sticky: they are written once and only a human edit clears them
    setup_reasons, desk_reason = evaluate_suspensions(resolved, policy)
    changed = False
    for setup, reason in setup_reasons.items():
        if setup not in suspended.setdefault("setups", {}):
            suspended["setups"][setup] = {"reason": reason, "at": now.isoformat()}
            changed = True
    if desk_reason and not suspended.get("desk"):
        suspended["desk"] = {"reason": desk_reason, "at": now.isoformat()}
        changed = True
    if changed:
        _write_json(suspended_path, suspended)
    total_rupees = sum(c["net_rupees"] for c in resolved)
    total_r = sum(c["net_r"] for c in resolved)
    status.update(
        state="suspended" if suspended.get("desk") else "active",
        approvals=[f"{s} on {m}" for s, m in policy.approvals if m == market],
        suspended=suspended,
        calls_today=[c for c in stored.values() if c["issued_on"] == latest_session.isoformat()],
        open_calls=open_calls,
        ledger={
            "resolved_calls": len(resolved),
            "net_rupees": round(total_rupees, 2),
            "net_r": round(total_r, 3),
            "wins": sum(1 for c in resolved if c["net_rupees"] > 0),
            "rule": (
                f"suspend at {policy.desk_floor_r:+.1f}R, or after {policy.checkpoint_calls} "
                "resolved calls if net rupees are not positive"
            ),
        },
        resolved_recent=resolved[-15:],
        sizing={"risk_pct": risk_pct, "capital": capital},
    )
    _write_json(root / "status.json", status)
    return status


def load_probation(market: str, state_dir: Path = STATE_DIR) -> dict[str, Any]:
    path = state_dir / market / "status.json"
    if not path.exists():
        return {"market": market, "state": "not_run"}
    return dict(json.loads(path.read_text(encoding="utf-8")))
