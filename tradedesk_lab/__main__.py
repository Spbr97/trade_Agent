"""Run with the existing interpreter; optional libraries live exclusively in lab/deps."""

from __future__ import annotations

import argparse
import sys

from tradedesk_lab.artifacts import OUTPUT, ROOT, verify_base


def main() -> None:
    sys.path.insert(0, str(OUTPUT / "deps"))
    parser = argparse.ArgumentParser(description="M14-M18 isolated research preview")
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train")
    train.add_argument("--no-cpcv", action="store_true")
    sub.add_parser("prepare")
    sub.add_parser("verify-base")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.command == "verify-base":
        print(verify_base())
        return
    if args.command == "serve":
        import uvicorn

        from tradedesk_lab.server import create_app

        uvicorn.run(create_app(ROOT, OUTPUT), host="127.0.0.1", port=args.port)
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
