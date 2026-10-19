from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="a Linux-only shim: Windows reads these names as real paths"
)

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "link_windows_paths", ROOT / "deploy" / "linux" / "link_windows_paths.py"
)
assert _spec and _spec.loader
link = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(link)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "crypto.duckdb").write_text("db", encoding="utf-8")
    (tmp_path / "tradedesk_lab").mkdir()
    (tmp_path / "tradedesk_lab" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    evidence = {
        "db_path": "data\\crypto.duckdb",
        "implementation": {
            "path": "C:\\Users\\sibap\\OneDrive\\Documents\\Trade_Agent\\tradedesk_lab\\mod.py"
        },
        "gone": "data\\not_here.json",
        "elsewhere": "D:\\other\\place.txt",
        "posix": "data/crypto.duckdb",
    }
    (tmp_path / "data" / "reports" / "manifest.json").write_text(
        json.dumps(evidence), encoding="utf-8"
    )
    return tmp_path


def test_links_resolve_windows_names_to_the_real_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    try:
        made, skipped = link.link_all(root)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    assert made == 2 and skipped == 2  # missing target and a drive outside the repo
    assert (root / "data\\crypto.duckdb").read_text(encoding="utf-8") == "db"
    abs_name = "C:\\Users\\sibap\\OneDrive\\Documents\\Trade_Agent\\tradedesk_lab\\mod.py"
    assert (root / abs_name).read_text(encoding="utf-8") == "x = 1\n"
    assert link.link_all(root) == (0, 2)  # idempotent


def test_dry_run_creates_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert link.link_all(root, dry_run=True)[0] == 2
    assert not (root / "data\\crypto.duckdb").exists()


def test_sqlite_registry_paths_link_inside_their_own_folder(tmp_path: Path) -> None:
    lab = tmp_path / "data" / "m14_m18"
    (lab / "runs" / "abc").mkdir(parents=True)
    (lab / "runs" / "abc" / "model.joblib").write_text("m", encoding="utf-8")
    con = sqlite3.connect(lab / "registry.sqlite")
    con.execute("CREATE TABLE candidates (artifact_path TEXT)")
    con.executemany(
        "INSERT INTO candidates VALUES (?)",
        [("runs\\abc\\model.joblib",), ("runs\\gone\\x.joblib",), ("runs/abc/model.joblib",)],
    )
    con.commit()
    con.close()
    try:
        made, skipped = link.link_all(tmp_path)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    assert (made, skipped) == (1, 1)
    assert (lab / "runs\\abc\\model.joblib").read_text(encoding="utf-8") == "m"
    assert link.link_all(tmp_path) == (0, 1)
