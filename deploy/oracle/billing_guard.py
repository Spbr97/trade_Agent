"""Watch the Oracle account every few minutes so a charge never goes unnoticed.

The owner cannot afford any Oracle bill (hard ceiling Rs 50-100, about SGD 0.75-1.45; the
account bills in SGD). Layers, strongest first:

1. The ``free-tier-only`` quota policy makes Oracle refuse anything outside the Always Free
   allowance. This script alerts if it is removed.
2. Resource watch: every OCID in the tenancy is compared with an accepted baseline. A new or
   resized resource is the *cause* of a charge and shows up within minutes, hours before the
   cost itself does. An unknown compute instance is stopped at once.
3. Cost watch: month-to-date cost from the Usage API. Anything above zero alerts. At
   KILL_SGD every running instance is stopped (the owner allows the jobs to stop).
4. Blind-spot alert: if Oracle cannot be reached for BLIND_MINUTES, that is alerted too, so
   silence never passes for an all-clear.

Alerts go through task_health.alert (desktop toast + ntfy phone push). A problem re-alerts
every REMIND_MINUTES until it clears. Exit code 1 while anything is wrong.

    python deploy/oracle/billing_guard.py            # one check (scheduled every 5 min)
    python deploy/oracle/billing_guard.py --accept   # record the current resources as known
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import oci

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "reports" / "oracle_billing.json"
BASELINE = ROOT / "data" / "reports" / "oracle_baseline.json"
QUOTA_NAME = "free-tier-only"
KILL_SGD = Decimal("0.30")  # about Rs 20, well under the Rs 50 ceiling
REMIND_MINUTES = 60
BLIND_MINUTES = 30
FREE_A1_OCPUS, FREE_A1_GB, FREE_STORAGE_GB = 2, 12, 200
GONE = {"TERMINATED", "TERMINATING", "DELETED", "DELETING"}


def _now() -> datetime:
    return datetime.now(UTC)


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def resources(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    search = oci.resource_search.ResourceSearchClient(config)
    details = oci.resource_search.models.StructuredSearchDetails(
        query="query all resources", matching_context_type="NONE"
    )
    found = oci.pagination.list_call_get_all_results(search.search_resources, details).data
    return {
        r.identifier: {"type": r.resource_type, "name": r.display_name or ""}
        for r in found
        if (r.lifecycle_state or "").upper() not in GONE
    }


def month_cost(config: dict[str, Any]) -> tuple[Decimal, str]:
    usage = oci.usage_api.UsageapiClient(config)
    now = _now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    details = oci.usage_api.models.RequestSummarizedUsagesDetails(
        tenant_id=config["tenancy"],
        time_usage_started=start,
        time_usage_ended=end,
        granularity="DAILY",
        query_type="COST",
    )
    items = usage.request_summarized_usages(details).data.items
    total = sum((Decimal(str(i.computed_amount or 0)) for i in items), Decimal(0))
    currency = next((i.currency for i in items if i.currency), "SGD")
    return total, currency


def check(config: dict[str, Any], baseline: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(problems, actions taken)."""

    tenancy = config["tenancy"]
    problems: list[str] = []
    actions: list[str] = []
    compute = oci.core.ComputeClient(config)

    quotas = oci.limits.QuotasClient(config).list_quotas(tenancy).data
    if not any(q.name == QUOTA_NAME for q in quotas):
        problems.append(f"quota policy '{QUOTA_NAME}' is MISSING - the hard block is gone")

    known = baseline.get("resources", {})
    for ocid, info in sorted(resources(config).items()):
        if ocid not in known:
            problems.append(f"NEW resource {info['type']} '{info['name']}'")

    for inst in compute.list_instances(tenancy).data:
        if inst.lifecycle_state in GONE:
            continue
        sc = inst.shape_config
        if inst.id not in known:
            if inst.lifecycle_state == "RUNNING":
                compute.instance_action(inst.id, "STOP")
                actions.append(f"stopped unknown instance '{inst.display_name}'")
        elif (
            inst.shape != "VM.Standard.A1.Flex"
            or (sc.ocpus or 0) > FREE_A1_OCPUS
            or (sc.memory_in_gbs or 0) > FREE_A1_GB
        ):
            problems.append(
                f"instance '{inst.display_name}' is {inst.shape} {sc.ocpus} OCPU "
                f"{sc.memory_in_gbs} GB - outside the free allowance"
            )

    block = oci.core.BlockstorageClient(config)
    ads = oci.identity.IdentityClient(config).list_availability_domains(tenancy).data
    total_gb = 0
    for ad in ads:
        for b in block.list_boot_volumes(availability_domain=ad.name, compartment_id=tenancy).data:
            if b.lifecycle_state not in GONE:
                total_gb += b.size_in_gbs or 0
    for v in block.list_volumes(compartment_id=tenancy).data:
        if v.lifecycle_state not in GONE:
            total_gb += v.size_in_gbs or 0
    if total_gb > FREE_STORAGE_GB:
        problems.append(f"block storage is {total_gb} GB - free allowance is {FREE_STORAGE_GB} GB")

    cost, currency = month_cost(config)
    if cost > 0:
        problems.append(f"CHARGE: {currency} {cost:.4f} this month (kill switch at {KILL_SGD})")
    if cost >= KILL_SGD:
        for inst in compute.list_instances(tenancy).data:
            if inst.lifecycle_state == "RUNNING":
                compute.instance_action(inst.id, "STOP")
                actions.append(f"KILL SWITCH stopped '{inst.display_name}'")
    return problems, actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--accept", action="store_true", help="record current resources as known")
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument(
        "--config",
        default=os.environ.get("OCI_CONFIG_FILE", oci.config.DEFAULT_LOCATION),
        help="OCI config file (the server keeps its own under /etc/tradedesk/oci)",
    )
    args = parser.parse_args(argv)

    from tradedesk.task_health import alert

    state = _load(STATE)
    now = _now()
    problems: list[str] = []
    actions: list[str] = []
    try:
        config = oci.config.from_file(file_location=args.config, profile_name=args.profile)
        if args.accept:
            found = resources(config)
            _save(BASELINE, {"accepted_at": now.isoformat(), "resources": found})
            print(f"baseline saved: {len(found)} resources")
            return 0
        baseline = _load(BASELINE)
        if not baseline:
            problems = ["no baseline yet - run with --accept once"]
        else:
            problems, actions = check(config, baseline)
        state.pop("blind_since", None)
    except Exception as exc:  # noqa: BLE001 - an unreachable Oracle is itself an alert
        since = datetime.fromisoformat(state.setdefault("blind_since", now.isoformat()))
        if now - since >= timedelta(minutes=BLIND_MINUTES):
            minutes = int((now - since).total_seconds() // 60)
            problems = [f"guard cannot reach Oracle for {minutes} min: {exc!r}"[:300]]
        print(f"{now.isoformat(timespec='seconds')} check failed: {exc!r}")

    key = "|".join(actions + problems)
    last = state.get("last_alert_at")
    due = key != state.get("last_alert_key") or bool(
        last and now - datetime.fromisoformat(last) >= timedelta(minutes=REMIND_MINUTES)
    )
    if key and due:
        alert("ORACLE BILLING ALERT", "\n".join(actions + problems), urgent=True)
        state.update(last_alert_key=key, last_alert_at=now.isoformat())
    elif not key and "blind_since" not in state and state.get("last_alert_key"):
        alert("Oracle billing: all clear", "No charge and no unknown resources.")
        state.update(last_alert_key="", last_alert_at=None)
    state.update(checked_at=now.isoformat(), problems=problems, actions=actions)
    _save(STATE, state)
    print(f"{now.isoformat(timespec='seconds')} problems={len(problems)} actions={len(actions)}")
    for line in actions + problems:
        print("  ", line)
    return 1 if key else 0


if __name__ == "__main__":
    sys.exit(main())
