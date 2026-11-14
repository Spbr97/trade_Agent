"""NSE's daily equity bhavcopy: every stock that traded each session, delisted ones included.

WHY (2026-11-14). Every lab study reads INDstocks' daily history, which holds only the stocks listed
today. A stock that crashed and was delisted, merged away or moved to another series is missing,
which flatters any rule that buys stocks (survivorship bias). NSE's archive keeps one file a
session with every traded security. This module downloads them once, keeps the mainboard equity
rows in one normalised form, and builds split- and bonus-adjusted daily panels for the stocks
today's list leaves out.

FACTS checked on real files the same day (do not re-derive):
  - Two formats. The old `cmDDMONYYYYbhav.csv.zip` exists until 2024-07-05; the UDiFF
    `BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` exists from at least 2024-01-02 on. A 404 on
    both is a session with no file.
  - PREVCLOSE is NOT adjusted on an ex-date: IRCTC's 1:5 split on 2021-10-28 shows PREVCLOSE
    4130.15, the raw close of the day before. Splits and bonuses come from the stored
    corporate_actions table (factor_num/factor_den multiply the earlier prices), which covers
    companies no longer listed. Some combined actions are parsed incompletely (a bonus and a split
    on one day record only one), so an unrecorded overnight gap that lands within 3% of a common
    split or bonus ratio is treated as one too (`UNRECORDED_RATIOS`); any other gap is real.
  - NSE's symbolchange.csv maps old symbols to new ones with an effective date. A row is renamed
    to the latest symbol only when the change took effect after the row's own date, so a symbol a
    new company reuses later is not merged into the old one.

Research data only; nothing here places an order or touches a live path.

    python -m tradedesk_lab.bhavcopy fetch      # resumable, about one file a second
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import time as time_module
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = OUTPUT / "bhavcopy"
DB = ROOT / "data" / "tradedesk.duckdb"
NIFTY = "NSE_40000001"
FIRST = date(2015, 9, 1)
SERIES = ("EQ", "BE", "BZ")  # mainboard equity; SME (SM/ST), bonds, ETFs and the rest are left out
FIELDS = ["day", "symbol", "series", "isin", "open", "high", "low", "close", "prevclose",
          "volume", "turnover"]  # fmt: skip
OLD_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/{y}/{mon}/cm{d:02d}{mon}{y}bhav.csv.zip"  # noqa: E501
)
NEW_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{y}{m:02d}{d:02d}_F_0000.csv.zip"  # noqa: E501
CHANGES_URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
NEW_FORMAT_FROM = date(2024, 7, 8)
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
# Price multipliers of the usual deep splits and bonuses (1:2, 1:4, 1:5, 1:10 splits; 1:1, 2:1,
# 3:1, 4:1 bonuses). Only ratios of a half or less: a 20% circuit or a results gap can open a stock
# at 0.8 or 0.67 of the last close, but not at half. An unrecorded overnight move this close to one
# of these is a share-count change; a recorded action always wins.
UNRECORDED_RATIOS = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10)
RATIO_TOLERANCE = 0.03


def day_path(day: date, store: Path = STORE) -> Path:
    return store / "raw" / str(day.year) / f"{day.isoformat()}.csv.gz"


def urls(day: date) -> list[str]:
    old = OLD_URL.format(y=day.year, mon=MONTHS[day.month - 1], d=day.day)
    new = NEW_URL.format(y=day.year, m=day.month, d=day.day)
    return [new, old] if day >= NEW_FORMAT_FROM else [old, new]


def _number(text: str | None) -> float:
    try:
        return float(text) if text not in (None, "", "-") else float("nan")
    except ValueError:
        return float("nan")


def parse(text: str, day: date) -> list[dict[str, Any]]:
    """The mainboard equity rows of one file, old or new format, in FIELDS order."""

    reader = csv.DictReader(io.StringIO(text))
    names = set(reader.fieldnames or ())
    out = []
    if "TckrSymb" in names:  # UDiFF, from 2024
        for r in reader:
            if r.get("SctySrs") in SERIES and r.get("Sgmt", "CM") == "CM":
                out.append({
                    "day": day.isoformat(), "symbol": r["TckrSymb"].strip(),
                    "series": r["SctySrs"], "isin": r.get("ISIN", ""),
                    "open": _number(r["OpnPric"]), "high": _number(r["HghPric"]),
                    "low": _number(r["LwPric"]), "close": _number(r["ClsPric"]),
                    "prevclose": _number(r["PrvsClsgPric"]),
                    "volume": _number(r["TtlTradgVol"]), "turnover": _number(r["TtlTrfVal"]),
                })  # fmt: skip
    elif "SYMBOL" in names:
        for r in reader:
            if r.get("SERIES", "").strip() in SERIES:
                out.append({
                    "day": day.isoformat(), "symbol": r["SYMBOL"].strip(),
                    "series": r["SERIES"].strip(), "isin": r.get("ISIN", ""),
                    "open": _number(r["OPEN"]), "high": _number(r["HIGH"]),
                    "low": _number(r["LOW"]), "close": _number(r["CLOSE"]),
                    "prevclose": _number(r["PREVCLOSE"]),
                    "volume": _number(r["TOTTRDQTY"]), "turnover": _number(r["TOTTRDVAL"]),
                })  # fmt: skip
    else:
        raise ValueError(f"{day}: unknown bhavcopy layout {sorted(names)[:6]}")
    return out


def _unzip(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read(z.namelist()[0]).decode("utf-8", errors="replace")


def _write_day(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def sessions_from_db(db: Path = DB, first: date = FIRST) -> list[date]:
    """The trading calendar: the benchmark index's daily candle dates (the project's rule)."""

    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        ts = con.execute(
            "SELECT ts FROM candles WHERE scrip_code = ? AND interval = '1day' ORDER BY ts",
            [NIFTY],
        ).fetchall()
    finally:
        con.close()
    days = pd.to_datetime([t for (t,) in ts], unit="s", utc=True).tz_convert("Asia/Kolkata").date
    return sorted({d for d in days if d >= first})


def fetch(
    sessions: Iterable[date], *, store: Path = STORE, pause_seconds: float = 0.5,
    client: Any = None,
) -> dict[str, int]:  # fmt: skip
    """Save each session's equity rows once. Resumable; a session with no file is recorded."""

    import contextlib

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    headers = dict(_NSE_HEADERS, Referer="https://www.nseindia.com/")
    missing_path = store / "missing.txt"
    missing = set(missing_path.read_text("utf-8").split()) if missing_path.exists() else set()
    counts = {"saved": 0, "missing": 0, "already": 0}
    errors = 0
    opened = (
        httpx.Client(headers=headers, timeout=60, follow_redirects=True)
        if client is None else contextlib.nullcontext(client)
    )  # fmt: skip
    with opened as client:
        for day in sessions:
            path = day_path(day, store)
            if path.exists() or day.isoformat() in missing:
                counts["already"] += 1
                continue
            text = None
            for url in urls(day):
                try:
                    response = client.get(url)
                except httpx.HTTPError as exc:
                    errors += 1
                    if errors >= 25:
                        raise RuntimeError(f"{errors} errors in a row, last: {exc}") from exc
                    time_module.sleep(min(60.0, pause_seconds * 2**errors))
                    break
                time_module.sleep(pause_seconds)
                if response.status_code == 200:
                    text = _unzip(response.content)
                    break
                if response.status_code != 404:
                    response.raise_for_status()
            else:
                if text is None:  # 404 on both formats
                    missing.add(day.isoformat())
                    store.mkdir(parents=True, exist_ok=True)
                    missing_path.write_text("\n".join(sorted(missing)) + "\n", "utf-8")
                    counts["missing"] += 1
                    continue
            if text is None:
                continue  # a transport error: retried on the next run
            errors = 0
            _write_day(parse(text, day), path)
            counts["saved"] += 1
    return counts


def fetch_changes(store: Path = STORE) -> Path:
    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    headers = dict(_NSE_HEADERS, Referer="https://www.nseindia.com/")
    response = httpx.get(CHANGES_URL, headers=headers, timeout=60, follow_redirects=True)
    response.raise_for_status()
    path = store / "symbolchange.csv"
    store.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, "utf-8")
    return path


def read_changes(path: Path) -> list[tuple[str, str, date]]:
    """(old, new, effective date) rows, oldest first."""

    out = []
    with path.open(encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            try:
                when = datetime.strptime(row[3].strip(), "%d-%b-%Y").date()
            except ValueError:
                continue
            out.append((row[1].strip(), row[2].strip(), when))
    return sorted(out, key=lambda r: r[2])


def canonical(symbol: str, on: date, changes: Sequence[tuple[str, str, date]]) -> str:
    """The latest name of `symbol` as it was on `on`: follows only changes effective after `on`."""

    name = symbol
    for old, new, when in changes:  # oldest first, so a chain A->B->C is followed in order
        if old == name and when > on:
            name = new
    return name


def read_rows(store: Path = STORE) -> pd.DataFrame:
    """Every saved row (one per day, symbol and series)."""

    import duckdb

    pattern = str(store / "raw" / "*" / "*.csv.gz")
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"SELECT * FROM read_csv('{pattern}', header=true, all_varchar=true)"
        ).df()
    finally:
        con.close()
    for k in FIELDS[4:]:
        frame[k] = pd.to_numeric(frame[k], errors="coerce")
    frame["day"] = pd.to_datetime(frame["day"])
    return frame


def one_row_a_day(frame: pd.DataFrame) -> pd.DataFrame:
    """Where a stock traded in two series on one day, keep EQ, then BE, then BZ."""

    rank = frame["series"].map({s: i for i, s in enumerate(SERIES)})
    return (frame.assign(_rank=rank).sort_values(["name", "day", "_rank"])
            .drop_duplicates(["name", "day"]).drop(columns="_rank"))  # fmt: skip


def _near_ratio(move: float) -> float | None:
    for ratio in UNRECORDED_RATIOS:
        if abs(move / ratio - 1) <= RATIO_TOLERANCE:
            return ratio
    return None


def adjust(stock: pd.DataFrame, actions: Mapping[date, float]) -> tuple[pd.DataFrame, list[str]]:
    """Back-adjust one stock's rows (sorted by day) for splits and bonuses. `actions` maps an
    ex-date to the price multiplier for earlier rows. Returns the rows and a note per change."""

    stock = stock.sort_values("day").reset_index(drop=True)
    days = stock["day"].dt.date.to_numpy()
    opens, closes = stock["open"].to_numpy(float), stock["close"].to_numpy(float)
    factor = np.ones(len(stock))
    notes = []
    for i in range(1, len(stock)):
        f = actions.get(days[i])
        if f is None and closes[i - 1] > 0 and np.isfinite(opens[i]) and opens[i] > 0:
            ratio = _near_ratio(opens[i] / closes[i - 1])
            if ratio is not None:
                f = ratio
                notes.append(f"{days[i]} unrecorded x{ratio:.4f}")
        elif f is not None:
            notes.append(f"{days[i]} recorded x{f:.4f}")
        if f is not None and f > 0:
            factor[:i] *= f
    out = stock.copy()
    for k in ("open", "high", "low", "close", "prevclose"):
        out[k] = out[k] * factor
    out["volume"] = out["volume"] / factor
    return out, notes


def actions_from_db(db: Path = DB) -> dict[str, dict[date, float]]:
    """symbol -> {ex-date: price multiplier} for every recorded split and bonus."""

    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT symbol, ex_date, factor_num, factor_den FROM corporate_actions "
            "WHERE kind IN ('split', 'bonus') AND factor_num > 0 AND factor_den > 0"
        ).fetchall()
    finally:
        con.close()
    out: dict[str, dict[date, float]] = {}
    for symbol, ex, num, den in rows:
        out.setdefault(symbol, {})[ex] = out.get(symbol, {}).get(ex, 1.0) * num / den
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradedesk_lab.bhavcopy")
    parser.add_argument("command", choices=("fetch",))
    parser.add_argument("--pause", type=float, default=0.5)
    args = parser.parse_args(argv)
    fetch_changes()
    counts = fetch(sessions_from_db(), pause_seconds=args.pause)
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
