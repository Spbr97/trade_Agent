"""Open a path that an evidence manifest recorded, on any operating system.

Manifests sealed on Windows store their artifact paths as ``data\m14_m18\...``. Linux reads
that as a single file name containing backslashes and finds nothing. The manifests are
hash-checked evidence and are never rewritten, so the reader normalises the separator instead.
Paths are relative to the working directory (the repo root for every scheduled job).
"""

from __future__ import annotations

from pathlib import Path


def stored_path(value: str | Path) -> Path:
    return Path(str(value).replace("\\", "/"))
