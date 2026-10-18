"""Pull the server's nightly backups (deploy/linux/backup.py) to this laptop.

Run by a laptop scheduled task. Copies every file in the server's manifest that is not here
yet (or differs), checks each against the manifest's sha256, and deletes local copies the
server no longer lists, so the laptop mirrors the server's last two weeks. Backups live
outside OneDrive on purpose (C:\\Users\\<you>\\tradedesk-backups): gigabytes of archives
should not sync to the cloud.

    python deploy/laptop/pull_backups.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from server_watch import HOST, KEY, _ssh  # noqa: E402

REMOTE = "/home/ubuntu/backups"
LOCAL = Path.home() / "tradedesk-backups"


def _scp() -> str:
    return _ssh().replace("ssh.exe", "scp.exe") if _ssh().endswith("ssh.exe") else "scp"


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, creationflags=flags
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    from tradedesk.task_health import alert

    ssh = [_ssh(), "-i", str(KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOST]
    listed = _run([*ssh, f"cat {REMOTE}/manifest.json"], 120)
    if listed.returncode != 0:
        print(f"no manifest yet or server unreachable: {listed.stderr.strip()[:200]}")
        return 0  # the server watch reports an unreachable server; nothing to pull yet
    manifest = json.loads(listed.stdout)["files"]
    LOCAL.mkdir(parents=True, exist_ok=True)
    pulled, bad = [], []
    for rel, meta in manifest.items():
        target = LOCAL / rel
        if (
            target.is_file()
            and target.stat().st_size == meta["bytes"]
            and sha256(target) == meta["sha256"]
        ):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        got = _run(
            [_scp(), "-i", str(KEY), "-o", "BatchMode=yes", f"{HOST}:{REMOTE}/{rel}", str(target)],
            3 * 3600,
        )
        if got.returncode != 0 or not target.is_file() or sha256(target) != meta["sha256"]:
            bad.append(rel)
            target.unlink(missing_ok=True)
        else:
            pulled.append(rel)
    for path in sorted(LOCAL.rglob("*.gz")):
        if path.relative_to(LOCAL).as_posix() not in manifest:
            path.unlink()
    for folder in sorted(LOCAL.glob("duckdb-*")):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    (LOCAL / "manifest.json").write_text(json.dumps({"files": manifest}, indent=1), "utf-8")
    print(f"pulled {len(pulled)}, failed {len(bad)}, kept {len(manifest)} files in {LOCAL}")
    if bad:
        alert("tradedesk backup pull failed", f"{len(bad)} file(s) did not copy: {bad[:3]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
