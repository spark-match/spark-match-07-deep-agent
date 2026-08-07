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

# Un solo texto para todos los 401 que no vienen de `AuthError`. Que sea el
# mismo es deliberado: distinguir "no mandaste token" de "el token no trae
# sub" le diria a quien sondea la API en que se ha quedado corto.
_UNAUTHENTICATED = "Missing or invalid authentication"


def _unauthorized(detail: str = _UNAUTHENTICATED) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail)


def _context_from_claims(claims: dict[str, Any], user_id: object) -> AuthContext:
    """Build the ``AuthContext`` from a claims mapping, or raise ``401``.

    Los dos caminos admitidos -- contexto del authorizer y token Bearer --
    terminan aqui, asi que la forma del contexto se decide en un solo sitio.
    El ``user_id`` llega ya resuelto porque cada camino lo saca de una clave
    distinta.
    """
    if not isinstance(user_id, str) or not user_id:
        raise _unauthorized()
    raw_email = claims.get("email")
    raw_role = claims.get("role")
    return AuthContext(
        user_id=user_id,
        email=raw_email if isinstance(raw_email, str) else "",
        role=str(resolve_role(raw_role if isinstance(raw_role, str) else None)),
    )


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
        return _context_from_claims(lambda_ctx, lambda_ctx.get("sub") or lambda_ctx.get("user_id"))

    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized()

    try:
        claims = decode_token(creds.credentials, await load_jwt_secret())
    except AuthError as exc:
        raise _unauthorized(str(exc)) from exc

    return _context_from_claims(claims, claims.get("sub"))


__all__ = ["require_auth"]
