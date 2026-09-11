"""Access-token management for INDstocks via TOTP (docs/indstocks-api.md, Users → Method 2).

Secrets (Client ID, MPIN, TOTP secret) live in the OS keychain through `keyring`; they are
never written to disk by this package. The access token lives only in memory.

Doc facts encoded here:
- POST /generate/token with header `x-api-key: <Client ID>` and body {"mpin", "totp"}.
- Success returns the token in a field named `token` (shape marked provisional: we also
  accept `access_token` and a nested `data`).
- One token is live at a time and lasts 24 h; generation is throttled to 1 per 60 s;
  wrong TOTP codes count toward a lockout. So: generate once, cache, never retry the same
  code, and back off on any failure.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import pyotp

KEYRING_SERVICE = "tradedesk-indstocks"
KEY_CLIENT_ID = "client_id"
KEY_MPIN = "mpin"
KEY_TOTP_SECRET = "totp_secret"

TOKEN_TTL_SECONDS = 24 * 3600
GENERATION_MIN_GAP_SECONDS = 60


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringStore:
    """Default store: the OS keychain (Windows Credential Manager here)."""

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self.service = service

    def get(self, key: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, key)

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, key)
        except PasswordDeleteError:
            pass


class MissingSecret(RuntimeError):
    pass


class TokenGenerationError(RuntimeError):
    """Any non-2xx from /generate/token. Carries the HTTP status; back off, don't retry."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"token generation failed ({status}): {message}")
        self.status = status


@dataclass
class Credentials:
    client_id: str
    mpin: str
    totp_secret: str

    @classmethod
    def from_store(cls, store: SecretStore) -> Credentials:
        values = {k: store.get(k) for k in (KEY_CLIENT_ID, KEY_MPIN, KEY_TOTP_SECRET)}
        missing = [k for k, v in values.items() if not v]
        if missing:
            raise MissingSecret(
                f"missing {missing} in keyring service {KEYRING_SERVICE!r}; "
                "run `tradedesk auth setup`"
            )
        return cls(
            values[KEY_CLIENT_ID] or "", values[KEY_MPIN] or "", values[KEY_TOTP_SECRET] or ""
        )

    def current_totp(self, at: float | None = None) -> str:
        return pyotp.TOTP(self.totp_secret).at(int(at if at is not None else time.time()))


def _extract_token(payload: Any) -> str | None:
    """Doc says `token`; the shape is provisional, so tolerate the obvious variants."""
    if not isinstance(payload, dict):
        return None
    for key in ("token", "access_token"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    data = payload.get("data")
    return _extract_token(data) if isinstance(data, dict) else None


@dataclass
class TokenProvider:
    """Caches one access token and regenerates it only when needed.

    `clock` is injectable for tests. Not safe across processes: only one process per
    account may generate tokens (each generation kills the previous token).
    """

    http: httpx.AsyncClient
    store: SecretStore = field(default_factory=KeyringStore)
    clock: Any = time.time
    _token: str | None = field(default=None, init=False)
    _issued_at: float | None = field(default=None, init=False)
    _last_attempt: float | None = field(default=None, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def set_token(self, token: str) -> None:
        """Use a dashboard-generated token instead of TOTP (handy for the first live check)."""
        self._token = token
        self._issued_at = self.clock()

    def invalidate(self) -> None:
        self._token = None
        self._issued_at = None

    @property
    def token(self) -> str | None:
        return self._token

    def _expired(self) -> bool:
        return self._issued_at is None or self.clock() - self._issued_at >= TOKEN_TTL_SECONDS

    async def get_token(self) -> str:
        async with self._lock:
            if self._token and not self._expired():
                return self._token
            return await self._generate()

    async def refresh(self) -> str:
        """Force a new token (after a TokenException). Still honours the 60 s throttle."""
        async with self._lock:
            self.invalidate()
            return await self._generate()

    async def _generate(self) -> str:
        now = self.clock()
        if self._last_attempt is not None:
            wait = GENERATION_MIN_GAP_SECONDS - (now - self._last_attempt)
            if wait > 0:
                await asyncio.sleep(wait)
        creds = Credentials.from_store(self.store)
        self._last_attempt = self.clock()
        resp = await self.http.post(
            "/generate/token",
            headers={"x-api-key": creds.client_id, "Content-Type": "application/json"},
            json={"mpin": creds.mpin, "totp": creds.current_totp(self.clock())},
        )
        try:
            payload = resp.json()
        except ValueError:
            payload = {"message": resp.text}
        if resp.status_code // 100 != 2:
            msg = payload.get("message") or payload.get("error") or resp.text
            raise TokenGenerationError(resp.status_code, str(msg))
        token = _extract_token(payload)
        if not token:
            raise TokenGenerationError(resp.status_code, f"no token in response: {payload}")
        self._token = token
        self._issued_at = self.clock()
        return token
