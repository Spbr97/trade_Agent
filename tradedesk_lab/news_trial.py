"""News-source trial: which feed, if any, sees big movers coming?

Approved 2026-10-14 with "take our time": several sources are compared side by side for a
few weeks before one is chosen. Sources live in config/news_sources.yaml and are polled only
once the user has checked that publisher's terms and enabled it.

- fetch (hourly): every enabled RSS/Atom feed; headlines stored with their own published
  time and the time we first saw them (data/m14_m18/news_trial/headlines/<date>.jsonl),
  matched to NSE companies by normalised company name.
- score (nightly): each source against the session's real +5% movers from the mover study -
  how many had a matching headline before the open, before the close, and how much else the
  source matched (noise). The report ranks sources only on what they would have shown in time.

Information only: no language model reads a headline and nothing here can create a signal,
alert or order (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab.artifacts import OUTPUT, ROOT

CONFIG = ROOT / "config" / "news_sources.yaml"
TRIAL_ROOT = OUTPUT / "news_trial"
SUFFIXES = {"limited", "ltd", "ltd.", "co", "co.", "company", "corporation", "corp"}
MIN_NAME_CHARS = 6
OPEN, CLOSE = time(9, 15), time(15, 30)


# ------------------------------------------------------------------------- sources


def load_sources(path: Path = CONFIG) -> list[dict[str, Any]]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [dict(s) for s in raw.get("sources") or []]


def enabled_sources(path: Path = CONFIG) -> list[dict[str, Any]]:
    return [s for s in load_sources(path) if s.get("enabled") is True]


# --------------------------------------------------------------------------- parse


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        stamp = parsedate_to_datetime(text)  # RSS pubDate
    except (TypeError, ValueError):
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))  # Atom
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    return stamp.astimezone(IST)


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    """RSS 2.0 <item> or Atom <entry> -> [{title, link, published}]."""

    root = ET.fromstring(xml_text)
    out = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: child for child in node}
        title = (fields["title"].text or "").strip() if "title" in fields else ""
        link_node = fields.get("link")
        link = ""
        if link_node is not None:
            link = (link_node.text or link_node.attrib.get("href") or "").strip()
        published = None
        for key in ("pubDate", "published", "updated", "date"):
            if key in fields:
                published = _when(fields[key].text)
                break
        if title:
            out.append({"title": " ".join(title.split()), "link": link, "published": published})
    return out


# ------------------------------------------------------------------------- matching


def _normalise(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def company_patterns(names: Mapping[str, str]) -> list[tuple[str, str]]:
    """(normalised company name, code) - legal suffixes and bracketed words dropped; names
    too short to be distinctive are skipped rather than allowed to match everything."""

    out = []
    for code, name in names.items():
        words = [w for w in _normalise(name).split() if w not in SUFFIXES]
        pattern = " ".join(words)
        if len(pattern) >= MIN_NAME_CHARS:
            out.append((pattern, code))
    return sorted(out, key=lambda p: -len(p[0]))


def match_companies(title: str, patterns: Sequence[tuple[str, str]]) -> list[str]:
    text = f" {_normalise(title)} "
    return sorted({code for pattern, code in patterns if f" {pattern} " in text})


def nse_company_names() -> dict[str, str]:
    from tradedesk.leader_discovery import _connect_read_only

    con = _connect_read_only(ROOT / "data" / "tradedesk.duckdb")
    try:
        rows = con.execute(
            "SELECT scrip_code, coalesce(name, symbol_name, trading_symbol) FROM instruments "
            "WHERE kind = 'equity' AND exch = 'NSE'"
        ).fetchall()
    finally:
        con.close()
    return {str(code): str(name) for code, name in rows if name}


# ---------------------------------------------------------------------------- fetch


def _key(source: str, item: Mapping[str, Any]) -> str:
    return hashlib.sha1(f"{source}|{item.get('link')}|{item.get('title')}".encode()).hexdigest()


def store(rows: Iterable[Mapping[str, Any]], root: Path = TRIAL_ROOT) -> int:
    by_day: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(str(row["seen_at"])[:10], {})[row["key"]] = dict(row)
    added = 0
    for day, fresh in by_day.items():
        path = root / "headlines" / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        known = set()
        if path.exists():
            known = {
                json.loads(line)["key"] for line in path.read_text("utf-8").splitlines() if line
            }
        new = [r for k, r in fresh.items() if k not in known]
        with path.open("a", encoding="utf-8") as stream:
            for r in new:
                stream.write(json.dumps(r, sort_keys=True, default=str) + "\n")
        added += len(new)
    return added


def fetch(now: datetime | None = None, *, sources: Sequence[Mapping[str, Any]] | None = None,
          root: Path = TRIAL_ROOT) -> dict[str, Any]:  # fmt: skip
    import httpx

    now = (now or datetime.now(IST)).astimezone(IST)
    sources = list(sources if sources is not None else enabled_sources())
    status: dict[str, Any] = {"fetched_at": now.isoformat(), "sources": {}}
    if not sources:
        status["note"] = "no source enabled yet (config/news_sources.yaml)"
        return status
    patterns = company_patterns(nse_company_names())
    rows = []
    headers = {"User-Agent": "tradedesk-personal-news-trial/1.0"}
    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
        for source in sources:
            name = str(source["name"])
            try:
                response = client.get(str(source["url"]))
                response.raise_for_status()
                items = parse_feed(response.text)
            except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the rest
                status["sources"][name] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
                continue
            for item in items:
                rows.append(
                    {
                        "key": _key(name, item),
                        "source": name,
                        "title": item["title"],
                        "link": item["link"],
                        "published_at": item["published"].isoformat()
                        if item["published"]
                        else None,
                        "seen_at": now.isoformat(),
                        "codes": match_companies(item["title"], patterns),
                    }
                )
            status["sources"][name] = {"ok": True, "items": len(items)}
    status["new_headlines"] = store(rows, root)
    path = root / "fetch_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


# ---------------------------------------------------------------------------- score


def _headline_time(row: Mapping[str, Any]) -> datetime:
    """When we could have known: the later of its published time and when we first saw it -
    a feed that back-dates or a slow poll can never make a headline look earlier."""

    seen = datetime.fromisoformat(str(row["seen_at"]))
    published = row.get("published_at")
    return max(seen, datetime.fromisoformat(published)) if published else seen


def load_headlines(start: datetime, end: datetime, root: Path = TRIAL_ROOT) -> list[dict[str, Any]]:
    out = []
    day = start.date()
    while day <= end.date():
        path = root / "headlines" / f"{day.isoformat()}.jsonl"
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                if line:
                    row = json.loads(line)
                    if start <= _headline_time(row) < end:
                        out.append(row)
        day += timedelta(days=1)
    return out


def score_session(
    session: date,
    movers: Sequence[str],
    headlines: Sequence[Mapping[str, Any]],
    sources: Sequence[str],
) -> dict[str, Any]:
    """Per source: movers with a matching headline before the open / before the close."""

    open_at = datetime.combine(session, OPEN, tzinfo=IST)
    mover_set = set(movers)
    out = {}
    for source in sources:
        mine = [h for h in headlines if h["source"] == source]
        pre_open = {c for h in mine if _headline_time(h) < open_at for c in h["codes"]}
        any_time = {c for h in mine for c in h["codes"]}
        out[source] = {
            "headlines": len(mine),
            "matched_companies": len(any_time),
            "movers": len(mover_set),
            "movers_seen_pre_open": len(mover_set & pre_open),
            "movers_seen_by_close": len(mover_set & any_time),
            "matched_non_movers": len(any_time - mover_set),
        }
    return {"session": session.isoformat(), "sources": out}


def score(session: date | None = None, *, root: Path = TRIAL_ROOT) -> dict[str, Any]:
    sources = [str(s["name"]) for s in enabled_sources()]
    obs_path = OUTPUT / "mover_study" / "nse" / "observations.jsonl"
    if not sources or not obs_path.exists():
        return {"status": "nothing_to_score", "enabled_sources": sources}
    observations = [json.loads(line) for line in obs_path.read_text("utf-8").splitlines() if line]
    if session is not None:
        observations = [o for o in observations if o["session"] == session.isoformat()]
    if not observations:
        return {"status": "no_observation", "session": str(session)}
    obs = observations[-1]
    day = date.fromisoformat(obs["session"])
    previous = day - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    start = datetime.combine(previous, CLOSE, tzinfo=IST)
    end = datetime.combine(day, CLOSE, tzinfo=IST)
    movers = [m["code"] for m in obs.get("movers", [])]
    result = score_session(day, movers, load_headlines(start, end, root), sources)
    path = root / "scores.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = path.read_text("utf-8") if path.exists() else ""
    if f'"session": "{result["session"]}"' not in seen:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, sort_keys=True) + "\n")
    report = summary(root)
    (root / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def summary(root: Path = TRIAL_ROOT) -> dict[str, Any]:
    path = root / "scores.jsonl"
    rows = (
        [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
        if path.exists()
        else []
    )
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        for source, s in row["sources"].items():
            t = totals.setdefault(source, {"sessions": 0, "movers": 0, "pre_open": 0, "by_close": 0,
                                           "headlines": 0, "noise": 0})  # fmt: skip
            t["sessions"] += 1
            t["movers"] += s["movers"]
            t["pre_open"] += s["movers_seen_pre_open"]
            t["by_close"] += s["movers_seen_by_close"]
            t["headlines"] += s["headlines"]
            t["noise"] += s["matched_non_movers"]
    ranked = [
        {
            "source": source,
            "sessions": int(t["sessions"]),
            "pre_open_coverage": t["pre_open"] / t["movers"] if t["movers"] else None,
            "by_close_coverage": t["by_close"] / t["movers"] if t["movers"] else None,
            "headlines_per_session": t["headlines"] / t["sessions"] if t["sessions"] else None,
            "non_mover_matches_per_session": t["noise"] / t["sessions"] if t["sessions"] else None,
        }
        for source, t in totals.items()
    ]
    ranked.sort(key=lambda r: -(r["pre_open_coverage"] or 0))
    return {"sources": ranked, "sessions_scored": len(rows), "authority": "information_only"}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradedesk_lab.news_trial")
    parser.add_argument("command", choices=["fetch", "score", "report"])
    args = parser.parse_args(argv)
    if args.command == "fetch":
        print(json.dumps(fetch(), indent=2, default=str))
    elif args.command == "score":
        print(json.dumps(score(), indent=2, default=str))
    else:
        print(json.dumps(summary(), indent=2, default=str))


if __name__ == "__main__":
    main()
