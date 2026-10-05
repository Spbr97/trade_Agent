"""Access-token management for INDstocks via TOTP (docs/indstocks-api.md, Users → Method 2).

Secrets (Client ID, MPIN, TOTP secret) live in the OS keychain through `keyring`; they are
never written to disk by this package. The access token is cached in the same keychain
(with its issue time) so that every `tradedesk` process reuses the one live token instead
of generating a new one: generation is throttled to 1/min and each generation kills the
previous token, so per-process tokens would make back-to-back commands fail.

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
import os
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import pyotp

KEYRING_SERVICE = "tradedesk-indstocks"
KEY_CLIENT_ID = "client_id"
KEY_MPIN = "mpin"
KEY_TOTP_SECRET = "totp_secret"
KEY_TOKEN = "token"
KEY_TOKEN_ISSUED_AT = "token_issued_at"
KEY_TOKEN_LAST_ATTEMPT = "token_last_attempt"

TOKEN_TTL_SECONDS = 24 * 3600
GENERATION_MIN_GAP_SECONDS = 60
TOKEN_GENERATION_LOCK_PATH = (
    Path(tempfile.gettempdir()) / "tradedesk-indstocks-token-generation.lock"
)


@asynccontextmanager
async def token_generation_lock(
    path: Path = TOKEN_GENERATION_LOCK_PATH,
) -> AsyncIterator[None]:
    """Serialize token generation across independently scheduled processes.

    The keyring cache is shared, but an asyncio lock is not.  After Windows resumes,
    several ``StartWhenAvailable`` tasks can otherwise observe the same empty/expired
    cache and all POST ``/generate/token`` together.  The broker permits only one token
    generation per minute and rejects the rest.

    This lock contains no credentials and is released by the OS if a process exits.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    await asyncio.sleep(0.1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


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

    `clock` is injectable for tests. The token and its timestamps are persisted in `store`
    so separate processes share one token; the 60 s throttle is honoured across processes
    too (the last attempt time is persisted). Only one machine per account should run
    tradedesk - a second machine generating tokens would kill this one's.
    """

    http: httpx.AsyncClient
    store: SecretStore = field(default_factory=KeyringStore)
    clock: Any = time.time
    generation_lock: Callable[[], AbstractAsyncContextManager[None]] = field(
        default=token_generation_lock, repr=False
    )
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
        self.store.delete(KEY_TOKEN)
        self.store.delete(KEY_TOKEN_ISSUED_AT)

    def _load_persisted(self) -> None:
        """Adopt a token another process generated, if it is still inside its 24 h."""
        if self._token:
            return
        token = self.store.get(KEY_TOKEN)
        issued = _as_float(self.store.get(KEY_TOKEN_ISSUED_AT))
        if token and issued is not None and self.clock() - issued < TOKEN_TTL_SECONDS:
            self._token, self._issued_at = token, issued
        if self._last_attempt is None:
            self._last_attempt = _as_float(self.store.get(KEY_TOKEN_LAST_ATTEMPT))

    def _reload_shared_state(self) -> bool:
        """Re-read keyring state after taking the cross-process generation lock."""

        token = self.store.get(KEY_TOKEN)
        issued = _as_float(self.store.get(KEY_TOKEN_ISSUED_AT))
        self._last_attempt = _as_float(self.store.get(KEY_TOKEN_LAST_ATTEMPT))
        if token and issued is not None and self.clock() - issued < TOKEN_TTL_SECONDS:
            self._token, self._issued_at = token, issued
            return True
        self._token, self._issued_at = None, None
        return False

    def _persist(self) -> None:
        if self._token and self._issued_at is not None:
            self.store.set(KEY_TOKEN, self._token)
            self.store.set(KEY_TOKEN_ISSUED_AT, repr(self._issued_at))

    @property
    def token(self) -> str | None:
        return self._token

    def _expired(self) -> bool:
        return self._issued_at is None or self.clock() - self._issued_at >= TOKEN_TTL_SECONDS

    async def get_token(self) -> str:
        async with self._lock:
            self._load_persisted()
            if self._token and not self._expired():
                return self._token
            return await self._generate()

    async def refresh(self, rejected_token: str | None = None) -> str:
        """Replace a rejected token without invalidating another process's winner.

        ``rejected_token`` is the credential that received TokenException.  When another
        process has already replaced it while this caller waits for the cross-process lock,
        adopt that replacement instead of deleting it and generating yet another token.
        Calls without an explicit token retain the historical force-refresh behaviour.
        """

        async with self._lock:
            expected = rejected_token or self._token or self.store.get(KEY_TOKEN)
            async with self.generation_lock():
                shared_is_valid = self._reload_shared_state()
                if (
                    rejected_token is not None
                    and shared_is_valid
                    and self._token != rejected_token
                ):
                    return self._token or ""

                shared_token = self.store.get(KEY_TOKEN)
                if expected is None or shared_token == expected:
                    self.store.delete(KEY_TOKEN)
                    self.store.delete(KEY_TOKEN_ISSUED_AT)
                self._token = None
                self._issued_at = None
                return await self._generate_locked(rejected_token=expected)

    async def _generate(self) -> str:
        async with self.generation_lock():
            # Another scheduled process may have generated and persisted a token while
            # this process waited for the lock.  Adopt it instead of issuing a second POST.
            if self._reload_shared_state():
                return self._token or ""  # narrowed by _reload_shared_state
            return await self._generate_locked()

    async def _generate_locked(self, *, rejected_token: str | None = None) -> str:
        """Generate while the caller owns ``generation_lock``."""

        now = self.clock()
        if self._last_attempt is not None:
            wait = GENERATION_MIN_GAP_SECONDS - (now - self._last_attempt)
            if wait > 0:
                await asyncio.sleep(wait)
                # Compatibility with a still-running pre-R2 process that does not take
                # this lock: prefer its newly persisted token after the wait, but never
                # re-adopt the exact credential that this refresh just rejected.
                if self._reload_shared_state() and self._token != rejected_token:
                    return self._token or ""
        creds = Credentials.from_store(self.store)
        self._last_attempt = self.clock()
        self.store.set(KEY_TOKEN_LAST_ATTEMPT, repr(self._last_attempt))
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
        self._persist()
        return token


def _as_float(v: str | None) -> float | None:
    try:
        return float(v) if v else None
    except ValueError:
        return None
