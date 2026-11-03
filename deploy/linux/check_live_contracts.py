"""After a deploy: does the code just installed still match every LIVE research contract?

`tradedesk_lab/forward.py::_contract_hashes` freezes the exact bytes of nine files for the
running forward cohort, and re-hashes them on every `tradedesk-after-close`. A deploy that
changes one of them - even a cosmetic line wrap, as on 2026-10-31 - is not caught by any test;
it surfaces at the next 16:00 run as `failed_stage: lab_forward`, and each day it stays broken
that day's forward evidence is lost for good once the 09:15 cutoff passes (2026-11-02 was).
Running this at the end of install.sh turns that into a failed deploy, in seconds, before any
evidence is at stake.

    python deploy/linux/check_live_contracts.py [--root /opt/tradedesk] [--no-alert]

Exit 0: every live contract matches (or no cohort has started). Exit 1: a mismatch, listed by
file, with a phone push unless --no-alert. The fix is to restore the frozen bytes
(`git checkout <commit-before-the-change> -- <file>`), never to edit the cohort's state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path


def mismatches(expected: Mapping[str, str], current: Mapping[str, str]) -> dict[str, str]:
    """File -> what went wrong, for every frozen file whose bytes no longer match."""

    out = {}
    for name, digest in expected.items():
        now = current.get(name)
        if now is None:
            out[name] = "missing from the deployed code"
        elif now != digest:
            out[name] = f"changed (cohort froze {digest[:12]}, deployed is {now[:12]})"
    return out


def check(root: Path, hashes: Callable[[], Mapping[str, str]]) -> dict[str, str]:
    state_path = root / "data" / "m14_m18" / "forward" / "state.json"
    if not state_path.exists():
        return {}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    expected = (state.get("activation") or {}).get("contract_sha256") or {}
    return mismatches(expected, hashes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--no-alert", action="store_true")
    args = parser.parse_args(argv)
    sys.path[:0] = [str(args.root), str(args.root / "src")]
    from tradedesk_lab import forward

    bad = check(args.root, forward._contract_hashes)
    if not bad:
        print("live contracts: forward cohort matches the deployed code")
        return 0
    lines = [f"{name}: {why}" for name, why in sorted(bad.items())]
    print("LIVE CONTRACT BROKEN - tradedesk-after-close will fail at lab_forward:")
    print("\n".join(f"  {line}" for line in lines))
    if not args.no_alert:
        from tradedesk.task_health import alert

        alert("Deploy broke the forward research contract",
              "Restore these files' frozen bytes today - each day it stays broken loses that "
              "day's forward evidence at the 09:15 cutoff:\n" + "\n".join(lines))  # fmt: skip
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
