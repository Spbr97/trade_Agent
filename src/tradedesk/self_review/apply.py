"""The ONLY code anywhere that turns an approved self-review proposal into an actual
config/code change. Every other module in this project that "self-analyses" (`signal_tracker
.flag_setup_failures`, `eod_learning`, `research_tracker.flag_research_findings`,
`prediction/calibration.check_and_flag_drift`) stops at `review_queue.add_item` on purpose -
this module is the deliberate, narrow exception the user explicitly asked for, and it is
built with the same care: it refuses to run unless a human has already approved the specific
item, and it refuses to touch anything outside a small, explicit, positively-asserted set of
files.

Guards, in order, all of which must pass or `apply()` raises instead of silently doing
nothing:
1. `item.status == "approved"` - never fires from a pending or rejected item.
2. The linked proposal file's live SHA-256 still matches `gauntlet_artifact_sha256`... no -
   it matches the proposal FILE's own hash recorded at submission time (a tamper/staleness
   check on the proposal itself, not the gauntlet artifact, which is immutable evidence and
   never re-hashed here).
3. `FORBIDDEN_PATHS`/`ALLOWED_PATH_PREFIXES`: every file this function is about to write is
   checked against a positive allowlist in the function body itself, not left to convention.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

from tradedesk import review_queue
from tradedesk.proposals import (
    ConfigPatch,
    NewDetectorCode,
    ProposalKind,
    RetireSetup,
    load_proposal,
    safe_filename,
)

APPLIED_DIR = Path("data/reviews/applied")
APPLIED_LOG = Path("docs/self-review-applied-log.md")


def _allowed_write_paths(root: Path) -> tuple[set[Path], tuple[Path, ...]]:
    """Positive allowlist: apply() refuses to write anywhere else, regardless of what a
    future proposal kind's target_files might claim. Parameterized by `root` (default the
    real repo, `tradedesk_lab.artifacts.ROOT`) purely so tests can point this at a throwaway
    git repo instead of ever touching the real one - the allowed RELATIVE paths themselves
    never change."""
    paths = {
        root / "config" / "setups.yaml",
        root / "src" / "tradedesk" / "engine" / "patterns.py",
        root / "src" / "tradedesk" / "engine" / "signals.py",
        root / "src" / "tradedesk" / "setups" / "__init__.py",
    }
    prefixes = (root / "src" / "tradedesk" / "setups",)
    return paths, prefixes


class ApplyRefused(Exception):
    pass


@dataclass(frozen=True)
class ApplyResult:
    item_id: str
    files_changed: list[dict[str, str]]
    git_commit_sha: str | None
    applied_at: str


def _guard_path(path: Path, root: Path) -> None:
    resolved = path.resolve()
    allowed_paths, allowed_prefixes = _allowed_write_paths(root)
    if resolved in allowed_paths:
        return
    if any(str(resolved).startswith(str(prefix)) for prefix in allowed_prefixes):
        return
    raise ApplyRefused(f"refusing to write outside the allowed set: {resolved}")


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return str(value)


def _patch_yaml_scalar(path: Path, setup: str, param: str, new_value: Any) -> str:
    """Surgical single-line patch: finds `  <setup>:` then, within its block, the
    `    <param>: <value>` line, and rewrites only that value - every other byte in the file,
    including its narrative comments, is preserved untouched. This is a controlled,
    known-schema file (config/setups.yaml), not a general YAML editor; a value with an
    inline comment or a non-scalar (list/mapping) is refused rather than mishandled."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_block = False
    setup_header = re.compile(rf"^  {re.escape(setup)}:\s*$")
    other_top_level = re.compile(r"^  \S")
    param_line = re.compile(rf"^(    {re.escape(param)}:\s*)(\S+)\s*$")

    for i, raw_line in enumerate(lines):
        stripped = raw_line.rstrip("\n")
        if setup_header.match(stripped):
            in_block = True
            continue
        if in_block:
            if other_top_level.match(stripped) and not stripped.startswith(f"  {setup}:"):
                break
            match = param_line.match(stripped)
            if match:
                prefix, old_raw = match.groups()
                lines[i] = f"{prefix}{_yaml_scalar(new_value)}\n"
                path.write_text("".join(lines), encoding="utf-8")
                return old_raw
    raise ApplyRefused(f"could not find setups.{setup}.{param} in {path}")


def _add_to_flow_list(path: Path, setup: str, key: str, value: str) -> list[str]:
    """Adds `value` to the setup's `    <key>: [a, b]` flow-style list, creating the line
    (right after the setup header) if it doesn't exist yet. Same surgical, line-based
    approach as `_patch_yaml_scalar`; returns the resulting list."""

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    setup_header = re.compile(rf"^  {re.escape(setup)}:\s*$")
    key_line = re.compile(rf"^    {re.escape(key)}:\s*\[(.*)\]\s*$")
    header_idx: int | None = None
    for i, raw_line in enumerate(lines):
        stripped = raw_line.rstrip("\n")
        if header_idx is None:
            if setup_header.match(stripped):
                header_idx = i
            continue
        is_comment = stripped.lstrip().startswith("#")
        if stripped.strip() and not stripped.startswith("    ") and not is_comment:
            break
        match = key_line.match(stripped)
        if match:
            items = [x.strip() for x in match.group(1).split(",") if x.strip()]
            if value not in items:
                items.append(value)
            lines[i] = f"    {key}: [{', '.join(items)}]\n"
            path.write_text("".join(lines), encoding="utf-8")
            return items
    if header_idx is None:
        raise ApplyRefused(f"could not find setups.{setup} in {path}")
    lines.insert(header_idx + 1, f"    {key}: [{value}]\n")
    path.write_text("".join(lines), encoding="utf-8")
    return [value]


def _insert_comment_above_setup(path: Path, setup: str, comment: str) -> None:
    """Inserts one `  # <comment>` line immediately above `  <setup>:`'s own header line,
    matching this file's existing convention of narrating every threshold change inline
    (see its own header comments). A no-op (raises) if the setup block can't be found."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    setup_header = re.compile(rf"^  {re.escape(setup)}:\s*$")
    for i, raw_line in enumerate(lines):
        if setup_header.match(raw_line.rstrip("\n")):
            lines.insert(i, f"  # {comment}\n")
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise ApplyRefused(f"could not find setups.{setup} in {path}")


def _insert_enum_member(path: Path, class_name: str, member_name: str, member_value: str) -> None:
    """Appends `    <MEMBER> = "<value>"` as the last member of `class <class_name>(...)`,
    right after that class's existing last member line - the same surgical,
    everything-else-untouched approach as the YAML patches above."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    class_header = re.compile(rf"^class {re.escape(class_name)}\(")
    member_line = re.compile(r"^\s+[A-Za-z_][A-Za-z0-9_]*\s*=")
    in_class = False
    last_member_idx: int | None = None
    for i, raw_line in enumerate(lines):
        if class_header.match(raw_line):
            in_class = True
            continue
        if in_class:
            if raw_line.strip() and not raw_line.startswith((" ", "\t")):
                break
            if member_line.match(raw_line):
                last_member_idx = i
    if last_member_idx is None:
        raise ApplyRefused(f"could not find any member of {class_name} in {path}")
    lines.insert(last_member_idx + 1, f'    {member_name} = "{member_value}"\n')
    path.write_text("".join(lines), encoding="utf-8")


def _insert_registry_entry(path: Path, module_name: str, class_name: str, enum_member: str) -> None:
    """Adds the import line (alphabetically, matching the existing block) and one
    REGISTRY dict entry to setups/__init__.py, both as targeted single-line insertions."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    import_line = f"from tradedesk.setups.{module_name} import {class_name}\n"
    import_re = re.compile(r"^from tradedesk\.setups\.\w+ import ")
    insert_at = None
    for i, raw_line in enumerate(lines):
        if import_re.match(raw_line):
            insert_at = i + 1
            if raw_line > import_line:
                insert_at = i
                break
    if insert_at is None:
        raise ApplyRefused(f"could not find any setup import in {path}")
    lines.insert(insert_at, import_line)

    registry_entry_re = re.compile(r"^\s+SetupKind\.\w+:\s*\w+\(\),\s*$")
    registry_close_re = re.compile(r"^}\s*$")
    last_entry_idx = None
    for i, raw_line in enumerate(lines):
        if registry_entry_re.match(raw_line):
            last_entry_idx = i
        elif last_entry_idx is not None and registry_close_re.match(raw_line):
            break
    if last_entry_idx is None:
        raise ApplyRefused(f"could not find REGISTRY entries in {path}")
    lines.insert(last_entry_idx + 1, f"    SetupKind.{enum_member}: {class_name}(),\n")
    path.write_text("".join(lines), encoding="utf-8")


def _append_setup_block(path: Path, setup: str, market: str) -> None:
    """Add a market-scoped, research-only setup block to ``setups.yaml``.

    Approval installs the detector for forward shadow measurement; it does not grant
    live-call rights. Removing ``research_only_markets`` requires a later accuracy review.
    """

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    top_level_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:\s*$")
    insert_at = None
    seen_setups = False
    for i, raw_line in enumerate(lines):
        stripped = raw_line.rstrip("\n")
        if stripped == "setups:":
            seen_setups = True
            continue
        if seen_setups and top_level_re.match(stripped):
            insert_at = i
            break
    if insert_at is None:
        raise ApplyRefused(f"could not find where the setups: block ends in {path}")
    lines.insert(
        insert_at,
        f"  {setup}:\n    enabled: true\n    markets: [{market}]\n"
        f"    research_only_markets: [{market}]\n",
    )
    path.write_text("".join(lines), encoding="utf-8")


def _apply_new_detector(
    payload: NewDetectorCode, item_id: str, *, root: Path, lab_output: Path = OUTPUT
) -> ApplyResult:
    signals_py = root / "src" / "tradedesk" / "engine" / "signals.py"
    setups_init = root / "src" / "tradedesk" / "setups" / "__init__.py"
    setups_yaml = root / "config" / "setups.yaml"
    new_setup_py = root / "src" / "tradedesk" / "setups" / f"{payload.setup_kind}.py"
    for path in (signals_py, setups_init, setups_yaml, new_setup_py):
        _guard_path(path, root)

    # A repeated approval used to insert the same enum member, import, registry entry and
    # YAML block twice, leaving Python unable to import SetupKind at all. Detector install
    # is deliberately one-shot; an already-present module must be reviewed, not re-added.
    if new_setup_py.exists():
        raise ApplyRefused(f"detector {payload.setup_kind!r} is already installed")

    production_source_path = (
        lab_output / "candidates" / payload.lab_experiment_id / "production_setup.py"
    )
    if not production_source_path.is_file():
        raise ApplyRefused(
            f"no pre-generated production source at {production_source_path} - "
            "detector_authoring.py only writes this on a gauntlet pass"
        )
    production_source = production_source_path.read_text(encoding="utf-8")

    before = {p: digest(p) if p.is_file() else None for p in (signals_py, setups_init, setups_yaml)}
    enum_member = payload.setup_kind.upper()
    class_name = "".join(part.capitalize() for part in payload.setup_kind.split("_"))

    _insert_enum_member(signals_py, "SetupKind", enum_member, payload.setup_kind)
    new_setup_py.write_text(production_source, encoding="utf-8")
    _insert_registry_entry(setups_init, payload.setup_kind, class_name, enum_member)
    _append_setup_block(setups_yaml, payload.setup_kind, payload.market)

    changed_paths = [signals_py, new_setup_py, setups_init, setups_yaml]
    sha = _commit(
        changed_paths,
        f"[self-review] add new detector {payload.setup_kind} on {payload.market} "
        f"(item {item_id}, lab experiment {payload.lab_experiment_id})",
        root=root,
    )
    files_changed = [
        {
            "path": str(p.relative_to(root)),
            "sha256_before": before.get(p),
            "sha256_after": digest(p),
        }
        for p in changed_paths
    ]
    return ApplyResult(
        item_id=item_id,
        files_changed=files_changed,
        git_commit_sha=sha,
        applied_at=datetime.now(UTC).isoformat(),
    )


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(paths: list[Path], message: str, *, root: Path) -> str:
    _git("add", *[str(p) for p in paths], cwd=root)
    _git(
        "commit",
        "-m",
        f"{message}\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
        cwd=root,
    )
    return _git("rev-parse", "HEAD", cwd=root)


def _apply_config_patch(payload: ConfigPatch, item_id: str, *, root: Path) -> ApplyResult:
    setups_yaml = root / "config" / "setups.yaml"
    _guard_path(setups_yaml, root)
    before_sha = digest(setups_yaml)
    param = payload.path.rsplit(".", 1)[-1]
    found_old = _patch_yaml_scalar(setups_yaml, payload.setup, param, payload.new_value)
    after_sha = digest(setups_yaml)
    sha = _commit(
        [setups_yaml],
        f"[self-review] tune {payload.setup} on {payload.market}: {param} "
        f"{found_old}->{payload.new_value} (item {item_id})",
        root=root,
    )
    return ApplyResult(
        item_id=item_id,
        files_changed=[
            {
                "path": str(setups_yaml.relative_to(root)),
                "sha256_before": before_sha,
                "sha256_after": after_sha,
            }
        ],
        git_commit_sha=sha,
        applied_at=datetime.now(UTC).isoformat(),
    )


def _apply_retire_setup(payload: RetireSetup, item_id: str, *, root: Path) -> ApplyResult:
    setups_yaml = root / "config" / "setups.yaml"
    _guard_path(setups_yaml, root)
    before_sha = digest(setups_yaml)
    today = datetime.now(UTC).date().isoformat()
    # Per-market only: setups.yaml serves every market, so flipping `enabled` would switch
    # the setup off on NSE/BSE too (and empty NSE's live scan). `retired_markets` removes it
    # from this market's active roster while it keeps being shadow-tracked there.
    comment = (
        f"RETIRED on {payload.market} {today} by self-review item {item_id}: sustained "
        "failure; still enabled elsewhere and shadow-tracked on "
        f"{payload.market} (see docs/self-review-applied-log.md)"
    )
    _insert_comment_above_setup(setups_yaml, payload.setup, comment)
    _add_to_flow_list(setups_yaml, payload.setup, "retired_markets", payload.market)
    after_sha = digest(setups_yaml)
    sha = _commit(
        [setups_yaml],
        f"[self-review] retire {payload.setup} on {payload.market} (item {item_id})",
        root=root,
    )
    return ApplyResult(
        item_id=item_id,
        files_changed=[
            {
                "path": str(setups_yaml.relative_to(root)),
                "sha256_before": before_sha,
                "sha256_after": after_sha,
            }
        ],
        git_commit_sha=sha,
        applied_at=datetime.now(UTC).isoformat(),
    )


def apply(
    item_id: str,
    *,
    root: Path = ROOT,
    lab_output: Path = OUTPUT,
    review_path: Path = review_queue.QUEUE,
    applied_dir: Path = APPLIED_DIR,
    applied_log: Path = APPLIED_LOG,
) -> ApplyResult:
    """`root` defaults to the real repository (`tradedesk_lab.artifacts.ROOT`) and is only
    ever overridden by tests, which point it at a throwaway git repo so this function's git
    commit and file writes never touch the real one."""

    rows = review_queue.load_queue(review_path)
    item = rows.get(item_id)
    if item is None:
        raise ApplyRefused(f"no such review item: {item_id}")
    if item.status != "approved":
        raise ApplyRefused(f"item {item_id} is {item.status!r}, not approved")
    if not item.proposal_ref:
        raise ApplyRefused(f"item {item_id} has no linked proposal")

    proposal_path = Path(item.proposal_ref)
    proposal = load_proposal(proposal_path)

    if proposal.kind == ProposalKind.CONFIG_PATCH:
        assert isinstance(proposal.payload, ConfigPatch)
        result = _apply_config_patch(proposal.payload, item_id, root=root)
    elif proposal.kind == ProposalKind.RETIRE_SETUP:
        # `replacement_source`, if set, is informational only (a pointer for a human to
        # follow up on separately - see retire_replace.py's docstring). Applying this item
        # only ever retires; it never auto-promotes a replacement.
        assert isinstance(proposal.payload, RetireSetup)
        result = _apply_retire_setup(proposal.payload, item_id, root=root)
    elif proposal.kind == ProposalKind.NEW_DETECTOR:
        assert isinstance(proposal.payload, NewDetectorCode)
        result = _apply_new_detector(proposal.payload, item_id, root=root, lab_output=lab_output)
    else:  # pragma: no cover - exhaustive per ProposalKind
        raise ApplyRefused(f"unknown proposal kind: {proposal.kind}")

    applied_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        applied_dir / f"{safe_filename(item_id)}.json",
        {
            "item_id": result.item_id,
            "decided_at": item.decided_at,
            "applied_at": result.applied_at,
            "proposal_kind": proposal.kind.value,
            "git_commit_sha": result.git_commit_sha,
            "files_changed": result.files_changed,
        },
    )
    applied_log.parent.mkdir(parents=True, exist_ok=True)
    with applied_log.open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n## {item.title}\n\n"
            f"- item: `{item_id}`\n"
            f"- applied: {result.applied_at}\n"
            f"- commit: `{result.git_commit_sha}`\n"
            f"- files: {', '.join(f['path'] for f in result.files_changed)}\n"
        )
    return result
