"""FastAPI dependency that validates the caller and produces ``AuthContext``
(Sprint 7, task 7.A).

Two supported paths, tried in order:

1. **API Gateway Lambda Authorizer context** — if the agent is deployed
   behind the same API Gateway as ``spark-match-03-backend``, the
   authorizer already validated the token and forwards its claims via
   ``request.scope["aws.event"]["requestContext"]["authorizer"]["lambda"]``.
   The backend trusts this blindly (no re-verification); we replicate that
   trust boundary only in that specific deployment topology.
2. **Direct Bearer token** — validated here via :mod:`src.auth.jwt_validator`
   against the secret from :mod:`src.auth.secret_loader`. This is the only
   path in a direct ECS/ALB deployment (no shared API Gateway authorizer).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth.context import AuthContext
from src.auth.jwt_validator import AuthError, decode_token
from src.auth.roles import resolve_role
from src.auth.secret_loader import load_jwt_secret

_bearer = HTTPBearer(auto_error=False)


def _authorizer_claims(request: Request) -> dict[str, Any] | None:
    """Extract claims forwarded by an upstream API Gateway Lambda Authorizer.

    Returns ``None`` when the agent isn't running behind that authorizer
    (the common case for direct ECS/ALB deployment and for local dev),
    falling through to Bearer token validation.
    """
    event = request.scope.get("aws.event")
    if not isinstance(event, dict):
        return None
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    lambda_ctx = authorizer.get("lambda") if isinstance(authorizer, dict) else None
    return lambda_ctx if isinstance(lambda_ctx, dict) else None


async def require_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
) -> AuthContext:
    """Resolve and validate the caller's identity, or raise ``401``."""
    if lambda_ctx := _authorizer_claims(request):
        user_id = lambda_ctx.get("sub") or lambda_ctx.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid authentication")
        raw_email = lambda_ctx.get("email")
        raw_role = lambda_ctx.get("role")
        return AuthContext(
            user_id=user_id,
            email=raw_email if isinstance(raw_email, str) else "",
            role=str(resolve_role(raw_role if isinstance(raw_role, str) else None)),
        )

    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid authentication")

    try:
        claims = decode_token(creds.credentials, await load_jwt_secret())
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid authentication")

    raw_email = claims.get("email")
    raw_role = claims.get("role")
    return AuthContext(
        user_id=sub,
        email=raw_email if isinstance(raw_email, str) else "",
        role=str(resolve_role(raw_role if isinstance(raw_role, str) else None)),
    )


__all__ = ["require_auth"]
