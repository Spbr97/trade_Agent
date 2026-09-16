"""Artifact IO and byte-level protection of the pre-existing workspace."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "m14_m18"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, default=str, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def protect() -> dict[str, str]:
    path = OUTPUT / "base_manifest.json"
    if path.exists():
        return cast(dict[str, str], json.loads(path.read_text(encoding="utf-8")))
    names = git("ls-files", "-z").split("\0")
    names += git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    manifest = {
        name: digest(ROOT / name)
        for name in names
        if name
        and (ROOT / name).is_file()
        and not name.startswith(("tradedesk_lab/", "tests_lab/", "docs/m14-m18-"))
    }
    write_json(path, manifest)
    return manifest


def verify_base() -> dict[str, Any]:
    manifest = protect()
    changed = [
        name
        for name, sha in manifest.items()
        if not (ROOT / name).is_file() or digest(ROOT / name) != sha
    ]
    return {"unchanged": not changed, "files_checked": len(manifest), "changed": changed}
