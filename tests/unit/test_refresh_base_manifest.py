from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "refresh_base_manifest", ROOT / "deploy" / "linux" / "refresh_base_manifest.py"
)
assert _spec and _spec.loader
refresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh)


def test_names_match_protects_rules(tmp_path: Path) -> None:
    for name in (
        "src/tradedesk/engine/engine.py",
        "config/setups.yaml",
        "tradedesk_lab/forward.py",  # the lab itself is never in its own base record
        "tests_lab/test_x.py",
        "docs/m14-m18-notes.md",
        "data/reports/health.json",  # runtime data is not code
        "src/tradedesk/__pycache__/x.cpython-312.pyc",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert refresh.deployed_names(tmp_path) == [
        "config/setups.yaml",
        "src/tradedesk/engine/engine.py",
    ]


def test_only_the_newest_deploy_backups_are_kept(tmp_path: Path) -> None:
    for stamp in ("20261018T001419Z", "20261018T002817Z", "20261018T003156Z", "20261019T081245Z"):
        (tmp_path / f"base_manifest.deploy-{stamp}.bak.json").write_text("{}", encoding="utf-8")
    (tmp_path / "base_manifest.json").write_text("{}", encoding="utf-8")
    removed = refresh.prune_backups(tmp_path, keep=2)
    assert removed == [
        "base_manifest.deploy-20261018T001419Z.bak.json",
        "base_manifest.deploy-20261018T002817Z.bak.json",
    ]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == [
        "base_manifest.deploy-20261018T003156Z.bak.json",
        "base_manifest.deploy-20261019T081245Z.bak.json",
        "base_manifest.json",  # the live record is never touched
    ]
