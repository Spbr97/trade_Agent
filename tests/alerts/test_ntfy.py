from __future__ import annotations

from typing import Any

from tradedesk.alerts import ntfy


class _Client:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        if self.fail:
            raise ConnectionError("phone unreachable")

        class _Response:
            def raise_for_status(self) -> None:
                return None

        return _Response()


def test_send_posts_body_and_ascii_title_to_the_topic() -> None:
    client = _Client()
    ok = ntfy.send("NSE picks ₹", "SHORT at 10:30 open", urgent=True, topic="t" * 30,
                   client=client)  # fmt: skip

    assert ok
    (call,) = client.calls
    assert call["url"] == f"{ntfy.SERVER}/{'t' * 30}"
    assert call["content"] == b"SHORT at 10:30 open"
    assert call["headers"]["Priority"] == "high"
    assert call["headers"]["Title"].isascii()


def test_send_is_best_effort_and_skips_without_a_topic(monkeypatch) -> None:
    assert ntfy.send("t", "b", topic="t" * 30, client=_Client(fail=True)) is False
    monkeypatch.setattr(ntfy, "load_topic", lambda: None)
    client = _Client()
    assert ntfy.send("t", "b", client=client) is False
    assert client.calls == []


def test_new_topic_is_long_and_unique() -> None:
    a, b = ntfy.new_topic(), ntfy.new_topic()
    assert a != b and len(a) > 30


def test_server_env_topic_wins_over_the_keychain(monkeypatch) -> None:
    monkeypatch.setenv(ntfy.ENV_VAR, " tradedesk-from-env ")
    assert ntfy.load_topic() == "tradedesk-from-env"
