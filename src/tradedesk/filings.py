"""Exchange filings: NSE corporate announcements, the time-stamped and authoritative "news"
behind many big moves (results, order wins, acquisitions, exchange clarifications).

Why filings rather than headlines: each one carries the exact time the company told the
exchange, so a study can only ever use a filing that existed before the moment it judges,
and no language model is needed to read it (CLAUDE.md: Claude output can never create a
signal). On 2026-10-14 the filings alone explained three of the day's five big movers -
results filed after the 13 Oct close (Tata Elxsi, TARIL) and one filed at 13:19 IST in the
middle of the session (Elecon, which then ran another 7%).

Storage: data/m14_m18/filings/<filing date>.jsonl, one line per filing, deduplicated by
the exchange's `seq_id`. Watch-only: nothing here alerts, sizes or trades.
"""

from __future__ import annotations

import argparse
import json
import os
import time as time_module
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST

FILINGS_ROOT = Path("data/m14_m18/filings")
NSE_HOME = "https://www.nseindia.com/"
NSE_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements"
SESSION_CLOSE = time(15, 30)
TEXT_CHARS = 300
MATERIAL = ("results", "order_win", "corporate_action", "news_clarification", "press_release")


@dataclass(frozen=True)
class Filing:
    symbol: str
    filed_at: str  # ISO, Asia/Kolkata
    category: str
    desc: str
    text: str
    seq_id: str
    attachment: str | None = None

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.filed_at)


def classify(desc: str, text: str) -> str:
    """A coarse, fixed category from the exchange's own subject line and summary."""

    d, t = desc.lower(), text.lower()
    if "financial result" in t or "financial result" in d:
        return "results"
    if any(
        k in d or k in t
        for k in (
            "bagging",
            "receiving of order",
            "award of order",
            "order received",
            "receipt of order",
            "letter of award",
        )
    ):
        return "order_win"
    if any(
        k in d
        for k in (
            "acquisition",
            "amalgamation",
            "merger",
            "scheme of arrangement",
            "buyback",
            "buy back",
            "open offer",
            "delisting",
            "preferential",
        )
    ):
        return "corporate_action"
    if "news verification" in d or "clarification" in d:
        return "news_clarification"
    if "press release" in d:
        return "press_release"
    return "routine"


def parse_rows(rows: Iterable[dict[str, Any]]) -> list[Filing]:
    out = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        stamp = str(row.get("sort_date") or "").strip()
        seq = str(row.get("seq_id") or "").strip()
        if not (symbol and stamp and seq):
            continue
        try:
            filed = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            continue
        desc = str(row.get("desc") or "").strip()
        text = " ".join(str(row.get("attchmntText") or "").split())[:TEXT_CHARS]
        out.append(
            Filing(
                symbol=symbol,
                filed_at=filed.isoformat(),
                category=classify(desc, text),
                desc=desc,
                text=text,
                seq_id=seq,
                attachment=row.get("attchmntFile") or None,
            )
        )
    return out


def fetch_nse(start: date, end: date, *, pause_seconds: float = 1.5) -> list[Filing]:
    """Filings filed on [start, end] (inclusive), one polite request per day."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    out: list[Filing] = []
    with httpx.Client(headers=_NSE_HEADERS, timeout=60, follow_redirects=True) as client:
        client.get(NSE_HOME)  # sets the cookies the API requires
        day = start
        while day <= end:
            stamp = day.strftime("%d-%m-%Y")
            response = client.get(
                NSE_ANNOUNCEMENTS_URL,
                params={"index": "equities", "from_date": stamp, "to_date": stamp},
            )
            response.raise_for_status()
            body = response.json()
            rows = body if isinstance(body, list) else body.get("data", [])
            out.extend(parse_rows(rows))
            day += timedelta(days=1)
            if day <= end:
                time_module.sleep(pause_seconds)
    return out


def store(filings: Iterable[Filing], root: Path = FILINGS_ROOT) -> dict[str, int]:
    """Merge into the per-date files (dedupe by seq_id). Returns rows per touched date."""

    by_day: dict[str, dict[str, Filing]] = {}
    for filing in filings:
        by_day.setdefault(filing.filed_at[:10], {})[filing.seq_id] = filing
    root.mkdir(parents=True, exist_ok=True)
    counts = {}
    for day, fresh in by_day.items():
        path = root / f"{day}.jsonl"
        merged = {f.seq_id: f for f in _read(path)}
        merged.update(fresh)
        rows = sorted(merged.values(), key=lambda f: (f.filed_at, f.seq_id))
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(
            "".join(json.dumps(asdict(f), sort_keys=True) + "\n" for f in rows), encoding="utf-8"
        )
        os.replace(temporary, path)
        counts[day] = len(rows)
    return counts


def _read(path: Path) -> list[Filing]:
    if not path.exists():
        return []
    return [Filing(**json.loads(line)) for line in path.read_text("utf-8").splitlines() if line]


def load(start: datetime, end: datetime, root: Path = FILINGS_ROOT) -> list[Filing]:
    """Filings filed in [start, end)."""

    out = []
    day = start.astimezone(IST).date()
    while day <= end.astimezone(IST).date():
        out.extend(f for f in _read(root / f"{day.isoformat()}.jsonl") if start <= f.when < end)
        day += timedelta(days=1)
    return sorted(out, key=lambda f: f.filed_at)


def previous_close(now: datetime) -> datetime:
    """The last weekday 15:30 IST strictly before `now` (exchange holidays are not known
    here, so after a holiday this reaches back one session too few - it never hides a
    filing, it only shows a few more)."""

    local = now.astimezone(IST)
    day = local.date()
    if local.time() <= SESSION_CLOSE:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime.combine(day, SESSION_CLOSE, tzinfo=IST)


def since_last_close(
    now: datetime | None = None,
    *,
    root: Path = FILINGS_ROOT,
    categories: Sequence[str] = MATERIAL,
) -> dict[str, Any]:
    now = (now or datetime.now(IST)).astimezone(IST)
    start = previous_close(now)
    rows = load(start, now + timedelta(seconds=1), root)
    material = [f for f in rows if f.category in categories]
    return {
        "since": start.isoformat(),
        "as_of": now.isoformat(),
        "total_filings": len(rows),
        "material": [asdict(f) for f in reversed(material)],
        "authority": "watch_only",
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradedesk.filings")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="fetch recent NSE filings into the store")
    fetch.add_argument("--days", type=int, default=2, help="today and the N-1 days before")
    fetch.add_argument("--from", dest="start", type=date.fromisoformat)
    fetch.add_argument("--to", dest="end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    today = datetime.now(IST).date()
    end = args.end or today
    start = args.start or end - timedelta(days=max(args.days, 1) - 1)
    counts = store(fetch_nse(start, end))
    print(json.dumps({"fetched_days": [start.isoformat(), end.isoformat()], "stored": counts}))


if __name__ == "__main__":
    main()
