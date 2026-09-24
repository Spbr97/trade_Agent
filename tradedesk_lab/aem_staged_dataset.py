"""Materialize the frozen 50-stock AEM baseline from the isolated M1 stage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import joblib
import pandas as pd

from tradedesk.config import load_config
from tradedesk.markets.market import nse_market
from tradedesk_lab.aem_accuracy_protocol import (
    DEFAULT_AEM_ACCURACY_PROTOCOL,
    AemAccuracyProtocol,
)
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT, AemContract
from tradedesk_lab.aem_dataset import AemDataset, build_events
from tradedesk_lab.aem_report import summarize_aem
from tradedesk_lab.aem_staged_data import read_staged_aem_source
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json


def prepare_staged_aem(
    root: Path = ROOT,
    lab_output: Path = OUTPUT,
    *,
    plan_id: str,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
    accuracy_protocol: AemAccuracyProtocol = DEFAULT_AEM_ACCURACY_PROTOCOL,
) -> AemDataset:
    """Build a diagnostic dataset without changing the legacy AEM latest pointer."""

    root, lab_output = Path(root).resolve(), Path(lab_output).resolve()
    settings = load_config(root)
    print("AEM staged: validating frozen source", flush=True)
    daily, minute, symbols, calendar, evaluation, source = read_staged_aem_source(
        root,
        lab_output,
        plan_id=plan_id,
        contract=contract,
        benchmark_symbol=settings.universe.benchmark,
    )
    print("AEM staged: reconstructing point-in-time events", flush=True)
    events, audit = build_events(
        daily,
        minute,
        symbols,
        risk=settings.risk,
        costs=nse_market(settings).costs,
        execution=minute,
        contract=contract,
        evaluation_sessions=len(evaluation),
        trading_dates=calendar,
        evaluation_dates=evaluation,
    )
    serial = events.copy()
    if "reasons" in serial:
        serial["reasons"] = serial["reasons"].map(json.dumps)
    dataset_sha256 = hashlib.sha256(
        pd.util.hash_pandas_object(serial, index=True).values.tobytes()
    ).hexdigest()
    diagnostics = summarize_aem(events, [str(day) for day in evaluation])
    overall = diagnostics["overall"]
    resolved = events.loc[events.status == "resolved"] if "status" in events else events.iloc[:0]
    dependencies = [
        "tradedesk_lab/aem_accuracy_protocol.py",
        "tradedesk_lab/aem_contract.py",
        "tradedesk_lab/aem_dataset.py",
        "tradedesk_lab/aem_detector.py",
        "tradedesk_lab/aem_features.py",
        "tradedesk_lab/aem_labels.py",
        "tradedesk_lab/aem_report.py",
        "tradedesk_lab/aem_staged_data.py",
        "tradedesk_lab/aem_staged_dataset.py",
        "tradedesk_lab/mcb_features.py",
        "src/tradedesk/engine/indicators.py",
        "src/tradedesk/backtest/null_baseline.py",
        "src/tradedesk/risk/costs.py",
        "src/tradedesk/risk/sizing.py",
        "src/tradedesk/markets/costs.py",
        "src/tradedesk/markets/market.py",
        "config/risk.yaml",
        "config/universe.yaml",
    ]
    run_id = uuid4().hex
    target = lab_output / "aem_staged" / "datasets" / run_id
    manifest = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "historical_diagnostic_only",
        "eligible_for_live": False,
        "plan_id": plan_id,
        "contract": contract.to_dict(),
        "contract_sha256": contract.sha256,
        "accuracy_protocol": accuracy_protocol.to_dict(),
        "accuracy_protocol_sha256": accuracy_protocol.sha256,
        "builder_sha256": digest(Path(__file__)),
        "risk_sha256": digest(root / "config/risk.yaml"),
        "data_store": str(root / "data/tradedesk.duckdb"),
        "evaluation_sessions": len(evaluation),
        "symbols_with_daily_and_m1": len(symbols),
        "events": len(events),
        "resolved_trades": len(resolved),
        "strict_success_rate": overall["strict_success_rate"],
        "mean_net_r": overall["mean_net_r"],
        "dataset_sha256": dataset_sha256,
        "source": source,
        "dependency_sha256": {name: digest(root / name) for name in dependencies},
        "evidence_class": "frozen_preperiod_selected_development_sample",
        "diagnostics": diagnostics,
        "audit": audit,
        "eligibility_assessment": {
            "status": "not_assessed",
            "passed": False,
            "reasons": [
                "Historical development evidence is not out-of-sample or prospective.",
                "Matched-random and registered stress results are not part of this dataset build.",
                "The frozen accuracy protocol must be applied after those artifacts exist.",
            ],
        },
        "limitations": [
            "Historical data was already inspected in development; this is not fresh evidence.",
            "The frozen 50-stock pilot does not establish reliability across all NSE stocks.",
            "Current metadata retains survivorship bias and historical revisions lack vintage.",
            "One incomplete upstream stock-session remains retained and explicitly rejected.",
            "Event returns do not yet apply simultaneous portfolio and sector constraints.",
            "No matched-random, cost-stress, portfolio or prospective gate is passed here.",
            "No order placement or production-call path is enabled by this artifact.",
        ],
    }
    target.mkdir(parents=True, exist_ok=False)
    joblib.dump(
        AemDataset(events=events, manifest=manifest, contract=contract),
        target / "dataset.joblib",
    )
    serial.to_csv(target / "events.csv", index=False)
    write_json(target / "manifest.json", manifest)
    write_json(
        lab_output / "aem_staged" / "latest.json",
        {"id": run_id, "path": str(target), "manifest": str(target / "manifest.json")},
    )
    return AemDataset(events=events, manifest=manifest, contract=contract)
