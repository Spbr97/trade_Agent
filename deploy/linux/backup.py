"""Nightly backup of everything under data/ on the server, for the laptop to pull.

Since the move the server holds the only live copy of the hash-chained research ledgers, so
losing the VM must not lose the evidence. Layout in OUT (owned by the SSH user, so the laptop
can pull it with scp):

  evidence-<monday>-full.tar.gz        once a week: data/ without the price databases and logs
  evidence-<date>-diff.tar.gz          every other night: what changed since that week's full
  duckdb-<monday>/<name>.duckdb.gz     once a week: a consistent copy of each price database
  manifest.json                        every file with its size and sha256

Restore = the newest full + the newest diff after it + the newest duckdb snapshot.
Price databases are copied through DuckDB itself (read-only ATTACH + COPY FROM DATABASE),
never as raw files, so a job writing at that moment cannot produce a torn copy.

Usage (root, from the systemd unit): python deploy/linux/backup.py --data /opt/tradedesk/data
    --out /home/ubuntu/backups --owner ubuntu
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

KEEP_WEEKS = 2
LOCK_RETRY_S, LOCK_TRIES = 15, 80  # wait out a writing job for up to 20 minutes
EXCLUDES = ("*.duckdb", "*.duckdb.wal", "*.duckdb.tmp", "data/logs", "*.snapshot.duckdb")


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tar_evidence(data: Path, out: Path, today: date) -> Path:
    monday = week_start(today)
    snar = out / f"evidence-{monday}.snar"
    full = out / f"evidence-{monday}-full.tar.gz"
    if full.exists() and snar.exists():
        target, listed = out / f"evidence-{today}-diff.tar.gz", out / ".diff.snar"
        shutil.copy2(snar, listed)  # differential: always relative to this week's full
    else:
        target, listed = full, snar
    excludes = [f"--exclude={pattern}" for pattern in EXCLUDES]
    subprocess.run(
        ["tar", f"--listed-incremental={listed}", "-czf", str(target), *excludes,
         "-C", str(data.parent), data.name],
        check=True,
    )  # fmt: skip
    if listed.name == ".diff.snar":
        listed.unlink()
    return target


def snapshot_duckdb(source: Path, target_dir: Path) -> Path:
    import duckdb

    target_dir.mkdir(parents=True, exist_ok=True)
    raw = target_dir / f"{source.stem}.snapshot.duckdb"
    raw.unlink(missing_ok=True)
    for attempt in range(LOCK_TRIES):
        try:
            con = duckdb.connect(":memory:")
            try:
                con.execute(f"ATTACH '{source}' AS src (READ_ONLY)")
                con.execute(f"ATTACH '{raw}' AS snap")
                con.execute("COPY FROM DATABASE src TO snap")
            finally:
                con.close()
            break
        except duckdb.IOException:
            raw.unlink(missing_ok=True)
            if attempt == LOCK_TRIES - 1:
                raise
            time.sleep(LOCK_RETRY_S)
    packed = target_dir / f"{source.name}.gz"
    with raw.open("rb") as src, gzip.open(packed, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    raw.unlink()
    return packed


def prune(out: Path, today: date) -> list[str]:
    oldest = week_start(today) - timedelta(weeks=KEEP_WEEKS - 1)
    removed = []
    for path in sorted(out.iterdir()):
        stamp = path.name.split("-", 1)[-1][:10]
        try:
            day = date.fromisoformat(stamp)
        except ValueError:
            continue
        if week_start(day) < oldest:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            removed.append(path.name)
    return removed


def write_manifest(out: Path) -> None:
    files = {}
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.suffix in {".gz"}:
            rel = path.relative_to(out).as_posix()
            files[rel] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    payload = {"written_at": datetime.now().astimezone().isoformat(), "files": files}
    (out / "manifest.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--owner", default="ubuntu")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    today = date.today()

    made = [tar_evidence(args.data, args.out, today).name]
    duck_dir = args.out / f"duckdb-{week_start(today)}"
    if not duck_dir.exists():
        for db in sorted(args.data.glob("*.duckdb")):
            made.append(snapshot_duckdb(db, duck_dir).relative_to(args.out).as_posix())
    removed = prune(args.out, today)
    write_manifest(args.out)
    shutil.chown(args.out, args.owner, args.owner)
    for path in args.out.rglob("*"):
        shutil.chown(path, args.owner, args.owner)
    print(json.dumps({"made": made, "pruned": removed}))


if __name__ == "__main__":
    main()
