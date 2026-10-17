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
