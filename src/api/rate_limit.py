"""Rate limiting for the AG-UI endpoint (Sprint 7, task 7.E.3).

Caps requests per authenticated ``user_id`` on ``POST /ag-ui`` — the
costliest endpoint in this API, since each call invokes the LLM. Falls back
to the client IP when no valid ``Authorization`` header is present (e.g. the
401 itself gets rate-limited too, preventing a brute-force credential-guess
loop from the same source).

Uses ``slowapi`` (in-process, per-worker limiter — see the note on
``budget_max_requests_per_user_per_day`` in ``src/config/settings.py`` for
why the *daily* budget is store-backed instead: a per-minute burst limiter
resetting on restart or differing slightly across ``--workers > 1`` is an
acceptable trade-off for abuse prevention, unlike a cost budget that must be
exact).
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from src.auth.jwt_validator import AuthError, decode_token
from src.auth.secret_loader import load_jwt_secret_sync


def _rate_limit_key(request: Request) -> str:
    """Key by the caller's ``user_id`` when the JWT is valid, else by IP.

    Best-effort decode only: this classifies traffic for rate limiting, it
    is not the source of truth for authentication (``require_auth`` in
    ``src/auth/dependencies.py`` is, and runs independently on every
    request). A malformed/missing token here just falls back to IP-based
    limiting rather than raising. Uses the synchronous secret loader because
    ``slowapi`` invokes ``key_func`` synchronously (no ``await`` support).
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return get_remote_address(request)

    token = auth_header[len("Bearer ") :]
    try:
        secret = load_jwt_secret_sync()
        claims = decode_token(token, secret)
    except AuthError:
        return get_remote_address(request)

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return get_remote_address(request)
    return f"user:{sub}"


limiter = Limiter(key_func=_rate_limit_key)

__all__ = ["limiter"]
