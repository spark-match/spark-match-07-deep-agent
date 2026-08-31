"""Security response headers middleware (Sprint 7, task 7.E.2).

Adds a fixed set of defensive headers to every response, mirroring what
``spark-match-03-backend`` (the TypeScript identity service) already sends,
so the two APIs the frontend talks to present a consistent security
posture. None of these headers require configuration or user input, so
they are added unconditionally rather than exposed as settings.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Fixed, non-configurable — see module docstring for why these aren't settings.
_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds standard security headers to every response."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response


__all__ = ["SecurityHeadersMiddleware"]
