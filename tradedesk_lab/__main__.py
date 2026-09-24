"""Run with the existing interpreter; optional libraries live exclusively in lab/deps."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from uuid import uuid4

from tradedesk_lab.artifacts import OUTPUT, ROOT, verify_base


def main() -> None:
    sys.path.insert(0, str(OUTPUT / "deps"))
    parser = argparse.ArgumentParser(description="M14-M18 isolated research preview")
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train")
    train.add_argument("--no-cpcv", action="store_true")
    sub.add_parser("prepare")
    sub.add_parser("prepare-clean")
    reliability = sub.add_parser("reliability")
    reliability.add_argument("--use-prepared", action="store_true")
    intraday = sub.add_parser("intraday-research")
    intraday.add_argument("--sessions", type=int, default=120)
    intraday.add_argument("--cohorts", type=int, default=200)
    mcb = sub.add_parser("mcb-prepare")
    mcb.add_argument("--sessions", type=int, default=120)
    aem = sub.add_parser("aem-prepare")
    aem.add_argument("--sessions", type=int, default=120)
    aem.add_argument("--as-of", type=date.fromisoformat)
    benchmark = sub.add_parser("aem-benchmark")
    benchmark.add_argument("--dataset-id")
    benchmark.add_argument("--cohorts", type=int, default=500)
    benchmark.add_argument("--seed", type=int, default=20260923)
    pilot = sub.add_parser("aem-universe-plan")
    pilot.add_argument("--dataset-id", required=True)
    pilot.add_argument("--shortlist-size", type=int, default=50)
    universe = sub.add_parser("nse-screen")
    universe.add_argument("--as-of", type=date.fromisoformat)
    universe.add_argument("--shortlist-size", type=int, default=50)
    universe.add_argument("--backfill-sessions", type=int, default=120)
    sub.add_parser("mcb-audit")
    sub.add_parser("verify-base")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8766)
    forward = sub.add_parser("forward")
    forward.add_argument("--watch", action="store_true")
    forward.add_argument("--interval-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.command == "aem-benchmark":
        if not 1 <= args.cohorts <= 5000 or not 0 <= args.seed < 2**32:
            parser.error("cohorts must be 1..5000 and seed must be uint32")
        from tradedesk_lab.aem_benchmark import run_benchmark

        report = run_benchmark(dataset_id=args.dataset_id, n_cohorts=args.cohorts, seed=args.seed)
        summary = {
            key: report.get(key)
            for key in ("status", "artifact_path", "observed_replay_verified", "grid_rows", "error")
        }
        if "comparison" in report:
            summary["actual"] = report["comparison"]["actual"]
            summary["comparison"] = report["comparison"]["comparison"]
        print(json.dumps(summary, indent=2))
        if report["status"].startswith("blocked_"):
            raise SystemExit(1)
        return
    if args.command == "aem-universe-plan":
        if not 1 <= args.shortlist_size <= 500:
            parser.error("shortlist size must be 1..500")
        from tradedesk_lab.aem_universe_plan import freeze_universe_plan

        report = freeze_universe_plan(
            dataset_id=args.dataset_id, shortlist_size=args.shortlist_size
        )
        preview = {
            key: report.get(key)
            for key in ("status", "artifact_path", "summary", "backfill_plan", "error")
        }
        if preview["backfill_plan"]:
            preview["backfill_plan"] = {
                key: value
                for key, value in preview["backfill_plan"].items()
                if key != "request_batches"
            }
        print(json.dumps(preview, indent=2))
        if report["status"].startswith("blocked_"):
            raise SystemExit(1)
        return
    if args.command == "nse-screen":
        if args.shortlist_size < 1 or args.backfill_sessions < 1:
            parser.error("shortlist size and backfill sessions must be positive")
        from tradedesk_lab.nse_universe import run_nse_screen

        report = run_nse_screen(
            as_of=args.as_of,
            shortlist_size=args.shortlist_size,
            backfill_sessions=args.backfill_sessions,
        )
        print(
            json.dumps(
                {
                    key: report.get(key)
                    for key in (
                        "status",
                        "artifact_path",
                        "summary",
                        "backfill_plan",
                        "error",
                        "recovery",
                    )
                },
                indent=2,
            )
        )
        if report["status"].startswith("blocked_"):
            raise SystemExit(1)
        return
    if args.command == "mcb-audit":
        from tradedesk_lab.mcb_data_audit import audit_mcb_data

        print(json.dumps(audit_mcb_data(), indent=2))
        return
    if args.command == "aem-prepare":
        if args.sessions < 1:
            parser.error("sessions must be positive")
        import duckdb

        from tradedesk_lab.aem_dataset import prepare_aem
        from tradedesk_lab.artifacts import write_json

        try:
            data = prepare_aem(sessions=args.sessions, as_of=args.as_of)
        except duckdb.IOException as exc:
            # A blocked read is not a new dataset and must not replace latest.json.
            target = OUTPUT / "aem" / "blocked" / f"{uuid4().hex}.json"
            report = {
                "status": "blocked_source_unavailable",
                "created_at": datetime.now(UTC).isoformat(),
                "eligible_for_live": False,
                "sessions_requested": args.sessions,
                "as_of_requested": str(args.as_of) if args.as_of else None,
                "error": str(exc),
                "recovery": (
                    "Retry after the existing database owner releases its connection. "
                    "Do not stop the live service or copy a database while it is being written."
                ),
                "artifact_path": str(target),
            }
            write_json(target, report)
            print(json.dumps(report, indent=2))
            raise SystemExit(1) from exc
        print(
            json.dumps(
                {
                    key: value
                    for key, value in data.manifest.items()
                    if key not in {"source", "dependency_sha256", "diagnostics"}
                },
                indent=2,
            )
        )
        return
    if args.command == "mcb-prepare":
        if args.sessions < 1:
            parser.error("sessions must be positive")
        from tradedesk_lab.mcb_dataset import prepare_mcb

        data = prepare_mcb(sessions=args.sessions)
        print(json.dumps(data.manifest, indent=2))
        return
    if args.command == "intraday-research":
        if args.sessions < 1 or args.cohorts < 1:
            parser.error("sessions and cohorts must be positive")
        from tradedesk_lab.intraday_research import run_research

        report = run_research(sessions=args.sessions, n_cohorts=args.cohorts)
        print(
            "Intraday diagnostic:", report["run_id"], "Live eligible:", report["eligible_for_live"]
        )
        return
    if args.command in {"prepare-clean", "reliability"}:
        from tradedesk_lab.clean_dataset import load_prepared, prepare_clean

        data = load_prepared() if getattr(args, "use_prepared", False) else prepare_clean()
        if args.command == "reliability":
            from tradedesk_lab.reliability import run_reliability

            report = run_reliability(data)
            print("Reliability diagnostic:", report["id"], report["eligibility"]["decision"])
        else:
            print(json.dumps(data.manifest, indent=2))
        return
    if args.command == "verify-base":
        print(verify_base())
        return
    if args.command == "serve":
        import uvicorn

        from tradedesk_lab.server import create_app

        uvicorn.run(create_app(ROOT, OUTPUT), host="127.0.0.1", port=args.port)
        return
    if args.command == "forward":
        from tradedesk_lab.forward import collect, watch

        if args.watch:
            watch(args.interval_seconds)
        else:
            state = collect()
            print(json.dumps(state["summary"], indent=2))
        return
    import joblib

    from tradedesk_lab.artifacts import write_json
    from tradedesk_lab.dataset import prepare
    from tradedesk_lab.runner import run

    cache = OUTPUT / "dataset.joblib"
    if args.command == "prepare" or not cache.exists():
        data = prepare()
        joblib.dump(data, cache)
        write_json(OUTPUT / "dataset.json", data.manifest)
    else:
        data = joblib.load(cache)
    if args.command == "train":
        report = run(data, stress=not args.no_cpcv)
        print("Champion:", report["champion"], "Decision:", report["eligibility"]["decision"])


if __name__ == "__main__":
    main()
