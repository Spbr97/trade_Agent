"""Send one phone digest (see src/tradedesk/digests.py).

    python scripts/send_digest.py close --market nse --job tradedesk-after-close \
        --result success --exit 0
    python scripts/send_digest.py eod --market nse
    python scripts/send_digest.py intraday --market nse
    python scripts/send_digest.py multiday            # every market
    python scripts/send_digest.py weekly

systemd runs it as ExecStopPost of the job it reports on, so it can never change that job's
result (`-` prefix) and a delivery failure only costs the message. A message already sent for
the same kind, market and day is not sent twice, except failures, which always go out.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk import digests  # noqa: E402
from tradedesk.broker.indstocks.models import IST  # noqa: E402
from tradedesk.task_health import alert  # noqa: E402

STATE = ROOT / "data" / "reports" / "digests_sent.json"
KEEP_DAYS_OF_STATE = 400


def _seen() -> dict[str, str]:
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _send(key: str, title: str, body: str, *, urgent: bool = False, force: bool = False) -> None:
    seen = _seen()
    if key in seen and not force:
        print(f"already sent: {key}")
        return
    delivered = alert(title, body, urgent=urgent)
    print(f"{'sent' if delivered else 'NOT delivered'}: {key}")
    if delivered:
        seen[key] = datetime.now(IST).isoformat(timespec="seconds")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(dict(sorted(seen.items())[-KEEP_DAYS_OF_STATE:])), "utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "kind",
        choices=["close", "eod", "intraday", "multiday", "weekly", "picks", "link", "drift"],
    )
    parser.add_argument("--market", choices=sorted(digests.CALLS))
    parser.add_argument("--job", default="")
    parser.add_argument("--result", default="success")
    parser.add_argument("--exit", dest="exit_status", default="0")
    args = parser.parse_args(argv)

    if args.kind not in {"close", "weekly"} and args.result != "success":
        print(f"{args.kind}: job result {args.result}, nothing sent (the health check reports it)")
        return 0
    now = datetime.now(IST)
    day = now.date().isoformat()
    root = ROOT
    if args.kind == "link":
        built = digests.dashboard_link(root=root)
        if built:
            alert(*built)  # sent every time the link changes; dashboard_link keeps the last one
            print(f"link pushed: {built[1].splitlines()[0]}")
        return 0
    if args.kind == "drift":
        # Earnings-drift calls (src/tradedesk/earnings_drift.py): issue, enter, exit, judge.
        from tradedesk import earnings_drift

        result = earnings_drift.run()
        print(json.dumps({k: v for k, v in result.items() if k != "verdict"}, default=str)[:2000])
        for n, (title, body) in enumerate(earnings_drift.messages(result)):
            _send(f"drift:{result['last_session']}:{n}", title, body)
        return 0
    if args.kind == "picks":
        # Consensus paper picks, each announced once (pick_announcer keeps its own record).
        from tradedesk.pick_announcer import announce_new_picks

        done = announce_new_picks(args.market or "crypto", now.date())
        print(f"picks announced: {len(done)}")
    elif args.kind == "weekly":
        title, body = digests.weekly(now.date(), root=root)
        _send(f"weekly:{now.isocalendar().year}-W{now.isocalendar().week}", title, body)
    elif args.kind == "multiday":
        for market in [args.market] if args.market else sorted(digests.CALLS):
            for n, (title, body) in enumerate(digests.multiday_calls(market, root=root)):
                _send(f"multiday:{market}:{day}:{n}", title, body)
    else:
        if not args.market:
            parser.error(f"{args.kind} needs --market")
        if args.kind == "close":
            title, body = digests.close_job(
                args.market, args.job, args.result, args.exit_status, today=now.date(), root=root
            )
            ok = args.result == "success"
            _send(f"close:{args.job}:{day}:{args.result}", title, body, urgent=not ok, force=not ok)
        elif args.kind == "eod":
            title, body = digests.eod_summary(args.market, now.date(), root=root)
            _send(f"eod:{args.market}:{day}", title, body)
        else:
            title, body = digests.intraday_check(args.market, root=root)
            _send(f"intraday:{args.market}:{day}", title, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
