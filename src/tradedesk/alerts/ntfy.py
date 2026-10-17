"""Phone push via ntfy (https://ntfy.sh): the alert channel for a headless server, where a
desktop toast reaches nobody.

The topic name is the only credential: anyone who knows it can read and post to it, so it is
a long random string kept in the keychain (service ``tradedesk-ntfy``), never in config or
code. ``tradedesk alerts setup-ntfy`` creates one and prints it for the phone app.
Best effort: a failed push is logged and returns False, never raises - a health check or a
pick announcement must not crash because a phone was unreachable. Messages carry research
and health text only; nothing here can create a signal or place an order.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import httpx

log = logging.getLogger(__name__)

SERVICE = "tradedesk-ntfy"
KEY = "topic"
SERVER = "https://ntfy.sh"
TIMEOUT_S = 10.0


def new_topic() -> str:
    return f"tradedesk-{secrets.token_urlsafe(24)}"


def load_topic() -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, KEY) or None
    except Exception as exc:  # noqa: BLE001 - no keyring backend means no channel
        log.warning("ntfy topic unavailable: %r", exc)
        return None


def store_topic(topic: str) -> None:
    import keyring

    keyring.set_password(SERVICE, KEY, topic)


def send(
    title: str,
    body: str,
    *,
    urgent: bool = False,
    topic: str | None = None,
    server: str = SERVER,
    client: Any = None,
) -> bool:
    topic = topic or load_topic()
    if not topic:
        return False
    headers = {
        # HTTP headers are latin-1; keep the title ASCII so a symbol can't break the send.
        "Title": title.encode("ascii", "replace").decode("ascii"),
        "Priority": "high" if urgent else "default",
        "Tags": "warning" if urgent else "chart_with_upwards_trend",
    }
    try:
        post = client.post if client is not None else httpx.post
        response = post(
            f"{server}/{topic}", content=body.encode("utf-8"), headers=headers,
            timeout=TIMEOUT_S,
        )  # fmt: skip
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - best effort, see module docstring
        log.warning("ntfy push not sent: %r", exc)
        return False
