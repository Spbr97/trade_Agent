"""Send one phone digest (see src/tradedesk/digests.py).

Since 2026-10-24 (owner): job successes, end-of-day numbers, NSE/BSE intraday studies and
movers are not pushed on their own - `multiday` sends one evening summary per market and
crypto `intraday` one morning push that carry them. Drift, review, weekly and failures
still push on their own.

    python scripts/send_digest.py close --market nse --job tradedesk-after-close \
        --result success --exit 0
    python scripts/send_digest.py eod --market nse
    python scripts/send_digest.py intraday --market nse
    python scripts/send_digest.py multiday            # every market
    python scripts/send_digest.py weekly
    python scripts/send_digest.py review           # review items waiting, each once, one push a run
    python scripts/send_digest.py movers --market nse
    python scripts/send_digest.py monthly --market crypto   # paper BTC/ETH plan, once a month

systemd runs it as ExecStopPost of the job it reports on, so it can never change that job's
result (`-` prefix) and a delivery failure only costs the message. A message already sent for
the same kind, market and day is not sent twice, except failures, which always go out.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk import digests, market_calendar  # noqa: E402
from tradedesk.broker.indstocks.models import IST  # noqa: E402
from tradedesk.config import load_config  # noqa: E402
from tradedesk.task_health import alert  # noqa: E402

STATE = ROOT / "data" / "reports" / "digests_sent.json"
KEEP_DAYS_OF_STATE = 400


def _seen() -> dict[str, str]:
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _send(
    key: str,
    title: str,
    body: str,
    *,
    urgent: bool = False,
    force: bool = False,
    also: Sequence[str] = (),
) -> None:
    """Send once per key; `also` names further keys the same message covers."""
    seen = _seen()
    if key in seen and not force:
        print(f"already sent: {key}")
        return
    delivered = alert(title, body, urgent=urgent)
    print(f"{'sent' if delivered else 'NOT delivered'}: {key}{f' (+{len(also)})' if also else ''}")
    if delivered:
        stamp = datetime.now(IST).isoformat(timespec="seconds")
        for k in (key, *also):
            seen[k] = stamp
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(dict(newest(seen, KEEP_DAYS_OF_STATE))), "utf-8")


def newest(seen: dict[str, str], keep: int) -> list[tuple[str, str]]:
    """The `keep` most recently sent keys. (Until 2026-10-20 the cut was by key name, so past
    400 entries a new close:/drift: key - alphabetically first - was dropped as it was saved
    and its same-day duplicate guard was lost.)"""
    return sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))[-keep:]


COLLECTION_HEALTH = ROOT / "data" / "m14_m18" / "accuracy_collection_health"
DATA_STAGES = ("data_load", "benchmark_load")


def data_ready(market: str, day: date, *, health: Path = COLLECTION_HEALTH) -> bool:
    """Whether `day`'s collection run for `market` loaded its prices - all the drift calls
    read. On 2026-10-19 a research-only check later in after-close failed after every price
    was in, and the job's failure held that evening's drift calls back until the next day."""
    try:
        report = json.loads((health / f"{market}.json").read_text(encoding="utf-8"))
        started = datetime.fromisoformat(report["started_at"]).astimezone(IST).date()
        status = {s["name"]: s["status"] for s in report["stages"]}
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return started == day and all(status.get(n) == "completed" for n in DATA_STAGES)


def review_batch(
    items: Sequence[tuple[str, str, str]], seen: Mapping[str, str]
) -> tuple[list[str], str, str] | None:
    """Every review item not yet announced, as one message: ``(keys, title, body)``. One push
    per job run instead of one per item (2026-10-19 sent fifteen in an evening)."""
    fresh = [item for item in items if item[0] not in seen]
    if not fresh:
        return None
    if len(fresh) == 1:
        key, title, body = fresh[0]
        return [key], title, body
    lines = [f"- {title.removeprefix('Review waiting ')}" for _, title, _ in fresh]
    reasons = list(dict.fromkeys(b.splitlines()[0] for _, _, b in fresh if b.strip()))
    body = "\n".join(
        [*lines, "", *reasons[:4], "Approve or reject on the dashboard: Review tab. "
         "Nothing changes until you do."]
    )  # fmt: skip
    return [key for key, _, _ in fresh], f"{len(fresh)} reviews waiting", body[:3500]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "kind",
        choices=[
            "close",
            "eod",
            "intraday",
            "multiday",
            "weekly",
            "picks",
            "link",
            "drift",
            "review",
            "movers",
            "monthly",
        ],
    )
    parser.add_argument("--market", choices=sorted(digests.CALLS))
    parser.add_argument("--job", default="")
    parser.add_argument("--result", default="success")
    parser.add_argument("--exit", dest="exit_status", default="0")
    args = parser.parse_args(argv)

    now = datetime.now(IST)
    # Review items wait for the owner whatever the job that found them did next; drift calls
    # need only the day's prices, so a later research-stage failure does not hold them back.
    # The evening summary and the crypto morning push go out even if their host job failed:
    # each section reads its own files and the failed part says so (one failure must not
    # silence the whole evening - 2026-10-24, when everything was folded into them).
    if (
        args.kind not in {"close", "weekly", "review", "multiday", "intraday"}
        and args.result != "success"
    ):
        if not (args.kind == "drift" and data_ready(args.market or "nse", now.date())):
            print(
                f"{args.kind}: job result {args.result}, nothing sent (the health check reports it)"
            )  # noqa: E501
            return 0
        print(f"drift: job result {args.result}, but today's prices loaded; sending the calls")
    day = now.date().isoformat()
    root = ROOT
    holiday = market_calendar.is_equity_holiday(
        now.date(), ROOT / "config" / "market_calendar.yaml"
    )
    if holiday and args.market in {"nse", "bse"} and args.kind != "review":
        print(f"{args.kind} {args.market}: NSE/BSE holiday, nothing sent")
        return 0
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
        from tradedesk import owner_trades

        synced = ROOT / owner_trades.LEDGER
        held = (
            owner_trades.open_drift_positions(owner_trades.read(synced))
            if synced.exists()
            else None
        )
        slots = int(float(load_config(ROOT).risk.trading_capital) // earnings_drift.AMOUNT_INR)
        for n, (title, body) in enumerate(earnings_drift.messages(result, held=held, slots=slots)):
            _send(f"drift:{result['last_session']}:{n}", title, body)
        return 0
    if args.kind == "monthly":
        # The paper BTC/ETH monthly plan (src/tradedesk/crypto_monthly.py): one push a month.
        from tradedesk import crypto_monthly

        result = crypto_monthly.run()
        print(json.dumps({k: v for k, v in result.items() if k != "prices"}, default=str)[:1000])
        built = crypto_monthly.message(result)
        if built:
            _send(f"monthly:{result['month']}", *built)
        return 0
    if args.kind == "review":
        batch = review_batch(digests.review_items(root=root), _seen())
        if batch is None:
            print("review: nothing new waiting")
        else:
            keys, title, body = batch
            _send(keys[0], title, body, also=keys[1:])
        return 0
    if args.kind == "movers":
        # Folded in (2026-10-24): NSE/BSE into the evening summary, crypto into the morning.
        push = "morning" if args.market == "crypto" else "evening"
        print(f"movers {args.market}: folded into the {push} push")
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
        # One evening summary per market instead of up to 13 multi-day messages (2026-10-24).
        ok = args.result == "success"
        for market in [args.market] if args.market else sorted(digests.CALLS):
            if holiday and market in {"nse", "bse"}:
                continue
            for n, (title, body) in enumerate(
                digests.evening_summary(market, now.date(), root=root, multiday_ok=ok)
            ):
                _send(f"evening:{market}:{day}:{n}", title, body)
    else:
        if not args.market:
            parser.error(f"{args.kind} needs --market")
        if args.kind == "close":
            title, body = digests.close_job(
                args.market, args.job, args.result, args.exit_status, today=now.date(), root=root
            )
            ok = args.result == "success"
            if ok:  # a job that finished fine is not news (2026-10-24); failures push at once
                print(f"close {args.job}: success, not pushed")
            else:
                _send(f"close:{args.job}:{day}:{args.result}", title, body, urgent=True, force=True)
        elif args.kind == "eod":
            print(f"eod {args.market}: folded into the evening summary")
        elif args.market == "crypto":
            for n, (title, body) in enumerate(digests.crypto_morning(root=root)):
                _send(f"morning:crypto:{day}:{n}", title, body)
        else:
            print(f"intraday {args.market}: folded into the evening summary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
