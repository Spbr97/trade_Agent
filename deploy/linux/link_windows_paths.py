r"""Make paths that Windows wrote into sealed evidence resolve on Linux, without editing anything.

Some evidence records its artifacts as ``data\crypto.duckdb`` or as an absolute
``C:\Users\...\Trade_Agent\tradedesk_lab\x.py``. The code that reads them is itself hashed into
that evidence, so it must stay byte-identical and cannot be patched. On Linux such a string is
just a file name containing backslashes, so for each one found this script creates a symlink
of that exact name in the repo root pointing at the real file. The evidence is never rewritten.

Usage: python deploy/linux/link_windows_paths.py [--root /opt/tradedesk] [--dry-run]
Idempotent; install.sh runs it after the data is in place.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DRIVE = re.compile(r"^[A-Za-z]:\\")
WIN_ROOT = re.compile(r"^[A-Za-z]:\\Users\\[^\\]+\\OneDrive\\Documents\\Trade_Agent\\", re.I)
MAX_JSON_BYTES = 30_000_000


def windows_paths(value: object, found: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            windows_paths(item, found)
    elif isinstance(value, list):
        for item in value:
            windows_paths(item, found)
    elif isinstance(value, str) and "\\" in value and "\n" not in value and len(value) < 400:
        if "/" not in value and (value.startswith(("data\\", "config\\")) or DRIVE.match(value)):
            found.add(value)


def target_for(value: str, root: Path) -> Path | None:
    relative = WIN_ROOT.sub("", value) if WIN_ROOT.match(value) else value
    if DRIVE.match(relative):
        return None  # a Windows location outside the repo: nothing on this machine to point at
    target = root / relative.replace("\\", "/")
    return target if target.exists() else None


def link_all(root: Path, *, dry_run: bool = False) -> tuple[int, int]:
    found: set[str] = set()
    for path in (root / "data").rglob("*.json"):
        try:
            if path.stat().st_size <= MAX_JSON_BYTES:
                windows_paths(json.loads(path.read_text(encoding="utf-8")), found)
        except (OSError, ValueError):
            continue
    made = skipped = 0
    for value in sorted(found):
        target = target_for(value, root)
        link = root / value
        if target is None:
            skipped += 1
        elif not link.is_symlink() and not link.exists():
            if not dry_run:
                link.symlink_to(target)
            made += 1
    return made, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    made, skipped = link_all(args.root, dry_run=args.dry_run)
    print(f"windows-path links: {made} created, {skipped} point at nothing here")


if __name__ == "__main__":
    main()
