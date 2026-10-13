"""Access-token management for Groww via API Key + TOTP (docs/groww-api.md).

Secrets (API key, TOTP secret) live in the OS keychain through `keyring`, mirroring
`broker/indstocks/auth.py`'s pattern almost exactly - kept as a separate, self-contained module
rather than sharing code with the INDstocks adapter, since these are two unrelated brokers and
coupling them for a ~15-line KeyringStore isn't worth it.

Doc facts encoded here, all flagged PROVISIONAL in docs/groww-api.md pending a real verified
call - update both this module and that doc together once confirmed live:
- POST /v1/token/api/access with header `Authorization: Bearer <api_key>` and body
  {"key_type": "totp", "totp": "<6-digit code>"}.
- Response carries the token in a field named `token` (shape marked provisional, same
  "tolerate the obvious variants" caution as INDstocks' own token response).
- Chosen over Groww's other "Access Token" flow (manual daily regeneration via the dashboard,
  expires 6 AM) specifically because this project runs unattended scheduled tasks - a token
  that needs a human to click a button every morning doesn't fit that shape. This is the same
  reasoning INDstocks' own TOTP-based flow was chosen for, and the code below is deliberately
  the same shape as broker/indstocks/auth.py's TokenProvider for that reason.
- Groww's docs don't state a generation throttle or token TTL the way INDstocks documents
  (1/min, 24h) - TOKEN_TTL_SECONDS below is a conservative placeholder until a real response
  is seen; there is no cross-process throttle wait here because none is documented (unlike
  INDstocks' GENERATION_MIN_GAP_SECONDS).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import pyotp

KEYRING_SERVICE = "tradedesk-groww"
KEY_API_KEY = "api_key"
KEY_TOTP_SECRET = "totp_secret"
KEY_TOKEN = "token"
KEY_TOKEN_ISSUED_AT = "token_issued_at"

BASE_URL = "https://api.groww.in"

# Not yet confirmed live - Groww's docs mention a token "expiry" field; until a real response
# is seen, treat any cached token as good for a conservative window and let a 401 force a
# refresh via TokenProvider.refresh(), the same fallback INDstocks' TokenGenerationError path
# relies on.
TOKEN_TTL_SECONDS = 12 * 3600


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
    """Any non-2xx from /v1/token/api/access. Carries the HTTP status; back off, don't retry."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"token generation failed ({status}): {message}")
        self.status = status


@dataclass
class Credentials:
    api_key: str
    totp_secret: str

    @classmethod
    def from_store(cls, store: SecretStore) -> Credentials:
        values = {k: store.get(k) for k in (KEY_API_KEY, KEY_TOTP_SECRET)}
        missing = [k for k, v in values.items() if not v]
        if missing:
            raise MissingSecret(
                f"missing {missing} in keyring service {KEYRING_SERVICE!r}; "
                "run `tradedesk auth groww setup`"
            )
        return cls(values[KEY_API_KEY] or "", values[KEY_TOTP_SECRET] or "")

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
    """Caches one access token and regenerates it only when needed. See module docstring for
    why the API Key + TOTP flow was chosen over Groww's manual daily "Access Token" flow."""

    http: httpx.AsyncClient
    store: SecretStore = field(default_factory=KeyringStore)
    clock: Any = time.time
    _token: str | None = field(default=None, init=False)
    _issued_at: float | None = field(default=None, init=False)

    def invalidate(self) -> None:
        self._token = None
        self._issued_at = None
        self.store.delete(KEY_TOKEN)
        self.store.delete(KEY_TOKEN_ISSUED_AT)

    def _load_persisted(self) -> None:
        if self._token:
            return
        token = self.store.get(KEY_TOKEN)
        issued = _as_float(self.store.get(KEY_TOKEN_ISSUED_AT))
        if token and issued is not None and self.clock() - issued < TOKEN_TTL_SECONDS:
            self._token, self._issued_at = token, issued

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
        self._load_persisted()
        if self._token and not self._expired():
            return self._token
        return await self._generate()

    async def refresh(self) -> str:
        self.invalidate()
        return await self._generate()

    async def _generate(self) -> str:
        creds = Credentials.from_store(self.store)
        resp = await self.http.post(
            "/v1/token/api/access",
            headers={
                "Authorization": f"Bearer {creds.api_key}",
                "Content-Type": "application/json",
            },
            json={"key_type": "totp", "totp": creds.current_totp(self.clock())},
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
