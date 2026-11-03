"""Which repo files are byte-sealed - check BEFORE editing one.

A sealed research cohort records the sha256 of the exact bytes of the modules AND the tests that
produced it, so reformatting such a file - even a line break or an import re-sort - makes its
digest check fail. The seal is not visible from the file itself, so grepping the file for
`digest(Path(__file__))` is not enough. There are TWO kinds, and both have already bitten:

  1. recorded in `docs/evidence/*.json`, asserted by a test - broke
     `tests_lab/test_osr_selector_race.py` on 2026-10-31 (reverted the same evening);
  2. listed in a LIVE contract that a running job re-hashes every day, where a mismatch is not
     a test failure but a job failure - `tradedesk_lab/forward.py::_contract_hashes` froze
     `tradedesk_lab/portable.py`, and wrapping one long line in it on 2026-10-31 made every
     `tradedesk-after-close` run fail at the `lab_forward` stage until 2026-11-04. A file can be
     absent from docs/evidence and still be sealed this way, which is exactly how it slipped
     through: kind 1 was checked, kind 2 did not yet exist here.

    uv run python scripts/sealed_files.py              # list every sealed file
    uv run python scripts/sealed_files.py path/to.py   # is this one file sealed?

Exit code 1 means the named file is sealed: do not reformat it. To change sealed logic, write a
new module and leave the sealed one alone, which is why each lab study owns its own entry point
rather than growing `tradedesk_lab/__main__.py`.
"""

from __future__ import annotations

import collections
import hashlib
import importlib
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"
# Running a script puts scripts/ on the path, not the repo root, so the contract modules below
# are not importable without this.
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
# `data/` holds sealed RUN OUTPUT (reports, manifests, trial files). Those are evidence, not
# code, and nobody edits them by hand, so this reports only source and config.
SKIP = {".venv", ".git", ".test-tmp-profitability", "node_modules", "__pycache__", "data"}
SUFFIXES = {".py", ".yaml", ".yml"}


def _hashes(obj: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _hashes(value, path + (str(key),))
    elif isinstance(obj, list):
        for n, value in enumerate(obj):
            yield from _hashes(value, path + (str(n),))
    elif isinstance(obj, str) and len(obj) == 64 and all(c in "0123456789abcdef" for c in obj):
        yield path, obj


def live_contracts() -> dict[str, str]:
    """File -> the live contract that re-hashes it on every run (kind 2 in the module docstring).

    Read from the contract function itself rather than copied, so a file added to one of these
    lists is covered here without anyone remembering to update this script.
    """

    out: dict[str, str] = {}
    for module, function in (("tradedesk_lab.forward", "_contract_hashes"),):
        try:
            mod = importlib.import_module(module)
            names = getattr(mod, function)()
        except Exception:  # noqa: BLE001 - a broken contract must not hide the rest
            continue
        for name in names:
            out[name] = f"{module}.{function}()"
    return out


def sealed_digests() -> dict[str, list[tuple[str, str]]]:
    """sha256 -> [(evidence file, field)] for every digest recorded under docs/evidence/."""

    out: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for path in sorted(EVIDENCE.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for field, digest in _hashes(data):
            out[digest].append((path.name, ".".join(field)))
    return out


def _head_bytes(key: str) -> bytes | None:
    try:
        done = subprocess.run(["git", "show", f"HEAD:{key}"], cwd=ROOT,  # noqa: S607
                              capture_output=True, check=False)  # fmt: skip
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def sealed_files() -> dict[str, tuple[list[tuple[str, str]], bool]]:
    """Repo file (posix-style, relative) -> (the evidence entries fixing its bytes, intact?).

    A file whose WORKING COPY no longer matches is still reported, with intact False, by falling
    back to its committed bytes - otherwise editing a sealed file would quietly remove it from
    this list, which is the one case where the answer matters most.
    """

    digests = sealed_digests()
    contracts = live_contracts()
    found: dict[str, tuple[list[tuple[str, str]], bool]] = {}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if SKIP & set(path.relative_to(ROOT).parts):
            continue
        key = path.relative_to(ROOT).as_posix()
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        entries = list(digests.get(current, ()))
        intact = bool(entries)
        if not entries and (head := _head_bytes(key)) is not None:
            entries = list(digests.get(hashlib.sha256(head).hexdigest(), ()))
        # A live contract seals the file whatever its current bytes are, so it is reported even
        # when the working copy has already drifted - the case that broke lab_forward.
        if key in contracts:
            entries.append((contracts[key], "live contract, re-hashed every run"))
            intact = True
        if entries:
            found[key] = (entries, intact)
    return found


def main(argv: list[str]) -> int:
    found = sealed_files()
    if argv:
        asked = Path(argv[0]).resolve()
        try:
            key = asked.relative_to(ROOT).as_posix()
        except ValueError:
            key = argv[0].replace("\\", "/")
        if key not in found:
            print(f"not sealed: {key}")
            return 0
        entries, intact = found[key]
        state = "do not reformat" if intact else "ALREADY MODIFIED - revert it (git checkout --)"
        print(f"SEALED - {state}: {key}")
        for name, field in entries:
            print(f"  {name}: {field}")
        return 1
    broken = [k for k, (_, intact) in found.items() if not intact]
    for key in sorted(found):
        entries, intact = found[key]
        print(f"{key} <- {entries[0][0]}{'' if intact else '   *** MODIFIED, REVERT ***'}")
    print(f"{len(found)} sealed files, {len(broken)} modified")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
