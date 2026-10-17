from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("keyrings.cryptfile")  # Linux-only dependency

from tradedesk.server_keyring import PASSWORD_ENV, EnvPasswordKeyring  # noqa: E402


def _keyring(tmp_path: Path) -> EnvPasswordKeyring:
    kr = EnvPasswordKeyring()
    kr.file_path = str(tmp_path / "crypted.cfg")
    return kr


def test_round_trips_without_ever_prompting(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(PASSWORD_ENV, "pw-from-systemd")
    monkeypatch.setattr(
        "getpass.getpass", lambda *a, **k: pytest.fail("a timer-started job cannot answer a prompt")
    )
    kr = _keyring(tmp_path)
    kr.set_password("svc", "key", "secret-value")
    assert kr.get_password("svc", "key") == "secret-value"
    raw = (tmp_path / "crypted.cfg").read_text(encoding="utf-8")
    assert "secret-value" not in raw  # encrypted on disk


def test_wrong_password_cannot_read_the_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(PASSWORD_ENV, "right")
    _keyring(tmp_path).set_password("svc", "key", "secret-value")
    monkeypatch.setenv(PASSWORD_ENV, "wrong")
    with pytest.raises(ValueError, match="Incorrect Password"):
        _keyring(tmp_path).get_password("svc", "key")
