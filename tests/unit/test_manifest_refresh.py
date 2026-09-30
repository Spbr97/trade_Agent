"""manifest_refresh: the auto-refresh must accept only drift explained by committed history."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from tradedesk_lab import artifacts

from tradedesk.self_review import manifest_refresh as mr


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip


def _commit(repo: Path, msg: str, when: float) -> None:
    _git(repo, "add", "-A")
    env = {**os.environ, "GIT_COMMITTER_DATE": f"{int(when)} +0000", "GIT_AUTHOR_DATE": f"{int(when)} +0000"}
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg],
        cwd=repo, check=True, capture_output=True, env=env,
    )  # fmt: skip


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/a.yaml").write_text("x: 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/self-review-applied-log.md").write_text("# log\n")
    (tmp_path / "tradedesk_lab").mkdir()
    (tmp_path / "tradedesk_lab/lab.py").write_text("a = 1\n")
    _commit(tmp_path, "init", time.time() - 1000)
    monkeypatch.setattr(artifacts, "ROOT", tmp_path)
    monkeypatch.setattr(artifacts, "OUTPUT", tmp_path / "data" / "m14_m18")
    artifacts.protect()
    return tmp_path


def _drift(repo: Path, rel: str, text: str) -> None:
    (repo / rel).write_text(text)


def test_unchanged_manifest_is_left_alone(repo: Path) -> None:
    r = mr.refresh_if_explained()
    assert not r.refreshed and "already matches" in r.reason


def test_committed_change_is_accepted_and_recorded(repo: Path) -> None:
    _drift(repo, "config/a.yaml", "x: 2\n")
    _commit(repo, "[self-review] change", time.time() + 10)
    r = mr.refresh_if_explained()
    assert r.refreshed and r.files == ["config/a.yaml"]
    assert artifacts.verify_base()["unchanged"]
    assert list((repo / "data/m14_m18").glob("base_manifest.auto-*.bak.json"))
    assert (repo / "data/m14_m18/manifest_refreshes.jsonl").read_text().count("\n") == 1


def test_uncommitted_edit_is_refused(repo: Path) -> None:
    _drift(repo, "config/a.yaml", "x: 2\n")
    r = mr.refresh_if_explained()
    assert not r.refreshed and r.unexplained == {"config/a.yaml": "uncommitted change"}
    assert not artifacts.verify_base()["unchanged"]


def test_commit_older_than_manifest_means_in_place_rewrite(repo: Path) -> None:
    # Drift with NO commit newer than the manifest, e.g. bytes changed then re-committed
    # "in the past": the file is clean but nothing reviewed produced the change.
    _drift(repo, "config/a.yaml", "x: 2\n")
    _commit(repo, "backdated", time.time() - 500)
    r = mr.refresh_if_explained()
    assert not r.refreshed and "no commit since" in r.unexplained["config/a.yaml"]


def test_deleted_file_is_refused(repo: Path) -> None:
    (repo / "config/a.yaml").unlink()
    r = mr.refresh_if_explained()
    assert not r.refreshed and r.unexplained["config/a.yaml"] == "file was deleted or moved"


def test_lab_code_never_auto_refreshes() -> None:
    assert mr._explain("tradedesk_lab/x.py", 0.0) == "lab code never auto-refreshes"
    assert mr._explain("tests_lab/x.py", 0.0) == "lab code never auto-refreshes"


def test_one_unexplained_file_blocks_the_whole_refresh(repo: Path) -> None:
    _drift(repo, "config/a.yaml", "x: 2\n")
    _commit(repo, "ok", time.time() + 10)
    _drift(repo, "docs/self-review-applied-log.md", "# rewritten\n")  # not append-only
    r = mr.refresh_if_explained()
    assert not r.refreshed and "docs/self-review-applied-log.md" in r.unexplained


def test_append_only_log_edit_is_accepted(repo: Path) -> None:
    with (repo / "docs/self-review-applied-log.md").open("a") as fh:
        fh.write("\n## RETIRE x\n")
    r = mr.refresh_if_explained()
    assert r.refreshed and r.files == ["docs/self-review-applied-log.md"]
