"""Private Telegram bot (PLAN.md 7, 16): sends the trade card with the chart image and
"Took it / Skip" buttons, and polls for the button presses. Raw Bot API over httpx - no
framework. The bot token lives in the keychain (`tradedesk-telegram` / `bot_token`); the
bot answers only the configured chat id.

Setup: talk to @BotFather -> /newbot -> copy the token -> `tradedesk alerts setup-telegram`.
Your chat id: message the bot once, then `tradedesk alerts telegram-chat-id` prints it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tradedesk.alerts.cards import OutboundMessage
from tradedesk.broker.indstocks.auth import KeyringStore

log = logging.getLogger(__name__)

TELEGRAM_SERVICE = "tradedesk-telegram"
KEY_BOT_TOKEN = "bot_token"
API = "https://api.telegram.org"

Decision = Callable[[str, str], Awaitable[None] | None]  # (signal_id, "took"|"skip")


def load_bot_token() -> str | None:
    return KeyringStore(TELEGRAM_SERVICE).get(KEY_BOT_TOKEN)


def store_bot_token(token: str) -> None:
    KeyringStore(TELEGRAM_SERVICE).set(KEY_BOT_TOKEN, token.strip())


@dataclass
class TelegramBot:
    token: str
    chat_id: int
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=30.0))
    sent: int = 0
    _offset: int = 0

    @property
    def base(self) -> str:
        return f"{API}/bot{self.token}"

    async def send(self, msg: OutboundMessage) -> bool:
        text = f"*{_md(msg.title)}*\n```\n{msg.body}\n```"
        markup: dict[str, Any] | None = None
        if msg.signal_id:
            markup = {
                "inline_keyboard": [
                    [
                        {"text": "Took it", "callback_data": f"took:{msg.signal_id}"},
                        {"text": "Skip", "callback_data": f"skip:{msg.signal_id}"},
                    ]
                ]
            }
        try:
            if msg.image and Path(msg.image).exists():
                data: dict[str, Any] = {
                    "chat_id": str(self.chat_id),
                    "caption": text[:1024],
                    "parse_mode": "Markdown",
                }
                if markup:
                    data["reply_markup"] = _json(markup)
                with Path(msg.image).open("rb") as fh:
                    r = await self.http.post(
                        f"{self.base}/sendPhoto",
                        data=data,
                        files={"photo": (Path(msg.image).name, fh, "image/png")},
                    )
            else:
                payload: dict[str, Any] = {
                    "chat_id": self.chat_id,
                    "text": text[:4096],
                    "parse_mode": "Markdown",
                }
                if markup:
                    payload["reply_markup"] = markup
                r = await self.http.post(f"{self.base}/sendMessage", json=payload)
            ok = r.status_code == 200 and bool(r.json().get("ok"))
            if not ok:
                log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
            else:
                self.sent += 1
            return ok
        except Exception as exc:  # noqa: BLE001 - alerts are best effort
            log.warning("telegram send error: %s", exc)
            return False

    async def poll_once(self, on_decision: Decision, timeout: int = 20) -> int:
        """One long-poll for button presses; returns how many decisions were handled."""
        r = await self.http.get(
            f"{self.base}/getUpdates",
            params={
                "offset": self._offset,
                "timeout": timeout,
                "allowed_updates": _json(["callback_query"]),
            },
            timeout=timeout + 10,
        )
        if r.status_code != 200:
            return 0
        handled = 0
        for upd in r.json().get("result", []):
            self._offset = max(self._offset, int(upd["update_id"]) + 1)
            cq = upd.get("callback_query")
            if not cq:
                continue
            chat = cq.get("message", {}).get("chat", {}).get("id")
            if chat != self.chat_id:
                log.warning("ignoring callback from unknown chat %s", chat)
                continue
            action, _, sid = str(cq.get("data", "")).partition(":")
            if action in ("took", "skip") and sid:
                res = on_decision(sid, action)
                if asyncio.iscoroutine(res):
                    await res
                handled += 1
                await self.http.post(
                    f"{self.base}/answerCallbackQuery",
                    json={
                        "callback_query_id": cq["id"],
                        "text": f"Recorded: {action} {sid.split(':')[1] if ':' in sid else sid}",
                    },
                )
        return handled

    async def poll_forever(self, on_decision: Decision, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.poll_once(on_decision)
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram poll error: %s", exc)
                await asyncio.sleep(5)

    async def my_chat_ids(self) -> list[int]:
        """Chat ids that have messaged the bot (to discover your own id during setup)."""
        r = await self.http.get(f"{self.base}/getUpdates", params={"timeout": 0})
        ids: list[int] = []
        for upd in r.json().get("result", []):
            chat = upd.get("message", {}).get("chat", {}).get("id")
            if chat is not None and chat not in ids:
                ids.append(int(chat))
        return ids


def _md(text: str) -> str:
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj)
