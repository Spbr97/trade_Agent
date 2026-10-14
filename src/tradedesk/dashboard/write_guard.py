"""Write protection for the dashboard.

The dashboard binds to 127.0.0.1, but the Cloudflare tunnel makes it reachable from the
internet by anyone holding the link. Every state-changing request (POST/PUT/PATCH/DELETE)
that arrived through the tunnel must therefore carry the dashboard token; a request made
directly on this PC needs none. cloudflared also connects from loopback, but it always adds
Cloudflare headers (cf-connecting-ip, cf-ray, x-forwarded-for...), which a browser talking to
127.0.0.1 directly never sends - so "loopback AND no tunnel header" means local.

The token lives in the OS keyring (service `tradedesk-dashboard`), never in code or config,
and is read per write request so rotating it needs no restart. With no token set, writes
through the tunnel are refused outright (fail closed). Reads are unchanged.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable, Mapping

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

SERVICE = "tradedesk-dashboard"
KEY = "write_token"
TOKEN_HEADER = "x-dashboard-token"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray", "cdn-loop", "x-forwarded-for", "forwarded")
LOOPBACK = frozenset({"127.0.0.1", "::1"})


def load_token() -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, KEY) or None
    except Exception:
        return None  # no usable keyring: behave as "no token set" (tunnel writes refused)


def create_token(*, rotate: bool = False) -> str:
    """The current token, creating one if none exists (or replacing it when `rotate`)."""

    import keyring

    existing = None if rotate else load_token()
    if existing:
        return existing
    token = secrets.token_urlsafe(18)
    keyring.set_password(SERVICE, KEY, token)
    return token


def is_direct_local(client_host: str | None, headers: Mapping[str, str]) -> bool:
    if client_host not in LOOPBACK:
        return False
    return not any(name in headers for name in TUNNEL_HEADERS)


def install(app: FastAPI, token_loader: Callable[[], str | None] | None = None) -> None:
    @app.middleware("http")
    async def guard_writes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in WRITE_METHODS and not is_direct_local(
            request.client.host if request.client else None, request.headers
        ):
            expected = (token_loader or load_token)()
            supplied = request.headers.get(TOKEN_HEADER, "")
            if not expected:
                return JSONResponse(
                    {
                        "error": "changes from outside this PC are disabled until a dashboard "
                        "token is set (run `tradedesk dashboard-token` on the PC)",
                        "token_required": True,
                    },
                    status_code=401,
                )
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                return JSONResponse(
                    {"error": "dashboard token required", "token_required": True},
                    status_code=401,
                )
        return await call_next(request)
