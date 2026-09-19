"""Run with the existing interpreter; optional libraries live exclusively in lab/deps."""

from __future__ import annotations

import argparse
import json
import sys

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
    sub.add_parser("mcb-audit")
    sub.add_parser("verify-base")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8766)
    forward = sub.add_parser("forward")
    forward.add_argument("--watch", action="store_true")
    forward.add_argument("--interval-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.command == "mcb-audit":
        from tradedesk_lab.mcb_data_audit import audit_mcb_data

        print(json.dumps(audit_mcb_data(), indent=2))
        return
    if args.command == "aem-prepare":
        if args.sessions < 1:
            parser.error("sessions must be positive")
        from tradedesk_lab.aem_dataset import prepare_aem

        data = prepare_aem(sessions=args.sessions)
        print(json.dumps(data.manifest, indent=2))
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
