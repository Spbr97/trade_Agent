"""Narrow, automatic refresh of the lab's base manifest before a self-review run.

`tradedesk_lab.artifacts.verify_base()` proves the research lab never touched production by
hashing every pre-existing file. That proof stops meaning anything if the manifest is
refreshed unconditionally, so this refreshes ONLY when every drifted file is explained by
reviewed, committed history:

* the file exists and is clean in git (no uncommitted edit), and
* its last commit is newer than the manifest (a real commit changed it, not an in-place
  rewrite), and
* it is not lab code (`tradedesk_lab/`, `tests_lab/`) - lab changes never auto-refresh.

`docs/self-review-applied-log.md` is the one exception to "clean": `apply()` appends to it
after its own commit, so an append-only working-tree diff (no deleted lines) is accepted.

Anything else - an uncommitted edit, a deleted file, lab code - leaves the manifest alone and
the run blocks exactly as before, for a human to review. Every refresh is recorded in
`data/m14_m18/manifest_refreshes.jsonl` (outside the hashed tree, so writing it cannot itself
cause drift) and the old manifest is kept as a timestamped backup."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tradedesk_lab import artifacts

APPEND_ONLY = "docs/self-review-applied-log.md"
NEVER_AUTO = ("tradedesk_lab/", "tests_lab/")


@dataclass
class RefreshResult:
    refreshed: bool
    reason: str
    files: list[str] = field(default_factory=list)
    unexplained: dict[str, str] = field(default_factory=dict)


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=artifacts.ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def _explain(name: str, manifest_mtime: float) -> str | None:
    """None when `name`'s drift is explained by committed history, else the reason it isn't."""
    if name.startswith(NEVER_AUTO):
        return "lab code never auto-refreshes"
    if not (artifacts.ROOT / name).is_file():
        return "file was deleted or moved"
    if _git("status", "--porcelain", "--", name):
        if name == APPEND_ONLY:
            stat = _git("diff", "--numstat", "--", name).split()
            if stat and stat[1] == "0":  # additions only
                return None
        return "uncommitted change"
    committed = _git("log", "-1", "--format=%ct", "--", name)
    if not committed:
        return "not tracked by git"
    if float(committed) < manifest_mtime:
        return "no commit since the manifest was written (changed in place)"
    return None


def refresh_if_explained(now: datetime | None = None) -> RefreshResult:
    state = artifacts.verify_base()
    if state["unchanged"]:
        return RefreshResult(False, "manifest already matches production")
    path = artifacts.OUTPUT / "base_manifest.json"
    manifest_mtime = path.stat().st_mtime
    unexplained: dict[str, str] = {}
    for name in state["changed"]:
        why = _explain(name, manifest_mtime)
        if why:
            unexplained[name] = why
    if unexplained:
        return RefreshResult(
            False,
            "drift not explained by committed history; needs human review",
            state["changed"],
            unexplained,
        )
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(path, artifacts.OUTPUT / f"base_manifest.auto-{stamp}.bak.json")
    path.unlink()
    artifacts.protect()
    after = artifacts.verify_base()
    record = {
        "at": (now or datetime.now(UTC)).isoformat(),
        "files": state["changed"],
        "head": _git("rev-parse", "HEAD"),
        "unchanged_after": after["unchanged"],
    }
    with (artifacts.OUTPUT / "manifest_refreshes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return RefreshResult(True, "refreshed: every drifted file is committed history", state["changed"])
