"""`/api/review*`: approving a self-review item (one with `proposal_ref` set) now applies
it immediately, per explicit user request - "approve should mean something, not just
bookkeeping." Every other review producer (no `proposal_ref`) is unchanged: approving is
still pure bookkeeping. Every test here monkeypatches the review-queue/self-review module
functions the endpoint lazily imports, pointed at tmp_path - never the real repo.

Each monkeypatch below captures the REAL function into a local variable first and has the
replacement lambda call that captured original - not `module.name(...)` again, which would
recurse into the just-installed replacement instead of the real implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

from tradedesk import review_queue as rq
from tradedesk.dashboard import DashboardState, create_app
from tradedesk.proposals import (
    ConfigPatch,
    ProposalKind,
    load_proposal,
    new_proposal,
    write_proposal,
)
from tradedesk.self_review import apply as self_review_apply
from tradedesk.self_review import decision_packet as dp

PASSING_REPORT = {"strategy_name": "x", "stopped_at": None, "kill_criteria": {"passed": True}}


def _make_config_patch_item(tmp_path: Path) -> tuple[str, Path]:
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"
    payload = ConfigPatch(
        setup="base_breakout", market="nse", path="setups.base_breakout.partial_at_r",
        old_value=2.0, new_value=2.5,
    )
    proposal = new_proposal(
        ProposalKind.CONFIG_PATCH, payload,
        gauntlet_report=PASSING_REPORT, gauntlet_artifact_path="x", gauntlet_artifact_sha256="y",
        evidence_summary="3 consecutive windows below floor",
    )
    item = rq.add_item("nse", "TUNE base_breakout", "detail", "proposal", path=review_path)
    proposal_path = write_proposal(proposal, item_id=item.id, directory=proposals_dir)
    rows = rq.load_queue(review_path)
    rows[item.id].proposal_ref = str(proposal_path)
    rq.save_queue(rows, review_path)
    return item.id, review_path


def _init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "setups.yaml").write_text(
        "setups:\n  base_breakout:\n    enabled: true\n    partial_at_r: 2.0\nentry:\n  x: 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)
    return root


def _patch_load_queue(monkeypatch, review_path: Path) -> None:
    real_load_queue = rq.load_queue
    monkeypatch.setattr(rq, "load_queue", lambda path=review_path: real_load_queue(review_path))


def _patch_decide(monkeypatch, review_path: Path) -> None:
    real_decide = rq.decide
    monkeypatch.setattr(
        rq, "decide", lambda item_id, status, path=review_path: real_decide(
            item_id, status, path=review_path
        )
    )


def _patch_decide_with_cooldown(monkeypatch, review_path: Path) -> None:
    real = dp.decide_with_cooldown
    monkeypatch.setattr(
        dp,
        "decide_with_cooldown",
        lambda item_id, status, review_path=review_path, cooldown_days=30: real(
            item_id, status, review_path=review_path, cooldown_days=cooldown_days
        ),
    )


async def test_plain_review_item_approve_is_still_pure_bookkeeping(
    tmp_path: Path, monkeypatch
) -> None:
    review_path = tmp_path / "queue.jsonl"
    item = rq.add_item("nse", "weekly review finding", "d", "p", path=review_path)
    _patch_load_queue(monkeypatch, review_path)
    _patch_decide(monkeypatch, review_path)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/review/decide", params={"item_id": item.id, "status": "approved"}
        )
        body = resp.json()

    assert resp.status_code == 200
    assert body["status"] == "approved"
    assert "applied" not in body
    reloaded = rq.load_queue(review_path)[item.id]
    assert reloaded.status == "approved"


async def test_self_review_approve_calls_apply_and_reports_the_commit(
    tmp_path: Path, monkeypatch
) -> None:
    root = _init_repo(tmp_path)
    item_id, review_path = _make_config_patch_item(tmp_path)
    applied_dir = tmp_path / "applied"
    applied_log = tmp_path / "log.md"

    _patch_load_queue(monkeypatch, review_path)
    _patch_decide_with_cooldown(monkeypatch, review_path)

    # The endpoint always passes root=/lab_output= explicitly (so it can be overridden by
    # monkeypatching self_review_apply.ROOT/OUTPUT in a simpler test) - here we instead
    # replace apply() outright and ignore whatever it was called with, always substituting
    # this test's own tmp_path-based root/review_path/applied_dir/applied_log. The `/api
    # /review` LIST endpoint separately reads the APPLIED_DIR module constant to compute
    # `applied`, so that must be monkeypatched too, not just the apply() function itself.
    real_apply = self_review_apply.apply
    monkeypatch.setattr(
        self_review_apply,
        "apply",
        lambda item_id, **_ignored: real_apply(
            item_id, root=root, review_path=review_path,
            applied_dir=applied_dir, applied_log=applied_log,
        ),
    )
    monkeypatch.setattr(self_review_apply, "APPLIED_DIR", applied_dir)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/review/decide", params={"item_id": item_id, "status": "approved"}
        )
        body = resp.json()

        assert resp.status_code == 200
        assert body["item"]["status"] == "approved"
        assert body["applied"]["git_commit_sha"]
        assert any(f["path"].endswith("setups.yaml") for f in body["applied"]["files_changed"])

        text = (root / "config" / "setups.yaml").read_text(encoding="utf-8")
        assert "partial_at_r: 2.5" in text

        listing = (await c.get("/api/review")).json()
        entry = next(i for i in listing if i["id"] == item_id)
        assert entry["applied"] is True

        gauntlet = (await c.get(f"/api/review/{item_id}/gauntlet")).json()
        assert gauntlet["kind"] == "config_patch"
        assert gauntlet["payload"]["new_value"] == 2.5


async def test_self_review_reject_stamps_cooldown_and_never_applies(
    tmp_path: Path, monkeypatch
) -> None:
    item_id, review_path = _make_config_patch_item(tmp_path)
    _patch_load_queue(monkeypatch, review_path)
    _patch_decide_with_cooldown(monkeypatch, review_path)
    apply_calls = []
    monkeypatch.setattr(
        self_review_apply, "apply", lambda *a, **kw: apply_calls.append((a, kw))
    )

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/review/decide", params={"item_id": item_id, "status": "rejected"}
        )
        body = resp.json()

    assert resp.status_code == 200
    assert body["status"] == "rejected"
    assert apply_calls == []  # apply() is never called on rejection
    reloaded = rq.load_queue(review_path)[item_id]
    proposal = load_proposal(Path(reloaded.proposal_ref))
    assert proposal.cooldown_until is not None


async def test_self_review_approve_reports_apply_error_without_hiding_it(
    tmp_path: Path, monkeypatch
) -> None:
    item_id, review_path = _make_config_patch_item(tmp_path)
    _patch_load_queue(monkeypatch, review_path)
    _patch_decide_with_cooldown(monkeypatch, review_path)

    def _boom(*a, **kw):
        raise self_review_apply.ApplyRefused("boom: production has drifted")

    monkeypatch.setattr(self_review_apply, "apply", _boom)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/review/decide", params={"item_id": item_id, "status": "approved"}
        )
        body = resp.json()

        assert resp.status_code == 409
        assert "boom" in body["apply_error"]
        assert body["item"]["status"] == "approved"  # the decision itself still happened
        listing = (await c.get("/api/review")).json()
        entry = next(i for i in listing if i["id"] == item_id)
        assert entry["applied"] is False  # but nothing was actually written
