"""Re-record the lab's base manifest for exactly the code being deployed.

`tradedesk_lab/artifacts.py::verify_base()` proves the research lab never alters production
files: any production file that differs from data/m14_m18/base_manifest.json pauses the lab's
replacement search until the record is refreshed. A deploy legitimately changes those files,
so install.sh calls this right after copying the code, and only then. Between deploys nothing
refreshes the record, so a change made on the server by anything else still pauses the search
and shows up in the health check, as before.

The file list is the deployed source tree (a `git archive` of a commit, so the same set
`protect()` takes from `git ls-files`), with protect()'s own exclusions. The previous record is
kept as base_manifest.deploy-<UTC time>.bak.json.

Usage: python deploy/linux/refresh_base_manifest.py --src ~/tradedesk --root /opt/tradedesk
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

EXCLUDED_PREFIXES = ("tradedesk_lab/", "tests_lab/", "docs/m14-m18-")
SKIPPED_TOP = {"data", ".venv", ".git"}


def deployed_names(src: Path) -> list[str]:
    names = []
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if path.is_file() and not path.is_symlink() and rel.parts[0] not in SKIPPED_TOP:
            name = rel.as_posix()
            if "__pycache__" not in rel.parts and not name.startswith(EXCLUDED_PREFIXES):
                names.append(name)
    return sorted(names)


def refresh(src: Path, root: Path) -> dict[str, object]:
    sys.path.insert(0, str(root))
    from tradedesk_lab.artifacts import OUTPUT, digest, verify_base, write_json

    record = OUTPUT / "base_manifest.json"
    backup = None
    if record.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = record.with_name(f"base_manifest.deploy-{stamp}.bak.json")
        shutil.copy2(record, backup)
    manifest = {
        name: digest(root / name) for name in deployed_names(src) if (root / name).is_file()
    }
    write_json(record, manifest)
    check = verify_base()
    return {
        "files": len(manifest),
        "unchanged": check["unchanged"],
        "backup": backup.name if backup else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(refresh(args.src.resolve(), args.root.resolve())))


if __name__ == "__main__":
    main()
