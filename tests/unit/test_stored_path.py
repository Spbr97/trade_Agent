from __future__ import annotations

from pathlib import Path

from tradedesk.stored_path import stored_path


def test_windows_separators_resolve_to_the_same_relative_path() -> None:
    windows = r"data\m14_m18\intraday_contract_race\crypto\manifests\a.jsonl.gz"
    assert stored_path(windows) == Path(
        "data/m14_m18/intraday_contract_race/crypto/manifests/a.jsonl.gz"
    )


def test_posix_and_path_inputs_are_unchanged() -> None:
    assert stored_path("data/x/y.json") == Path("data/x/y.json")
    assert stored_path(Path("data") / "x") == Path("data/x")
