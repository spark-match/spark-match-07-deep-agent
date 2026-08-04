"""JWT validation for tokens issued by ``spark-match-03-backend`` (Sprint 7, task 7.A).

The backend issues HS256 tokens with a fixed issuer/audience contract (see
``../spark-match-03-backend/docs/auth-rbac.md``). The signing key is the
**raw UTF-8 bytes** of the Secrets Manager ``SecretString`` — no
base64-decoding or JSON parsing (hard rule #6 in AGENTS.md).
"""

from __future__ import annotations

import jwt
from jwt import InvalidTokenError

JWT_ISSUER = "spark-match-backend"
JWT_AUDIENCE = "spark-match-api"
JWT_ALGORITHM = "HS256"


class AuthError(Exception):
    """Raised when a token fails validation (missing, expired, bad signature)."""


def decode_token(token: str, secret: bytes) -> dict[str, object]:
    """Validate a JWT issued by spark-match-03-backend and return its claims.

    Args:
        token: The raw bearer token (without the ``Bearer `` prefix).
        secret: Raw UTF-8 bytes of the shared HS256 signing key.

    Raises:
        AuthError: If the token is missing required claims, expired, has an
            invalid signature, or a wrong issuer/audience.
    """
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub"]},
        )
    except InvalidTokenError as exc:
        raise AuthError("Invalid or expired token") from exc


__all__ = ["JWT_ALGORITHM", "JWT_AUDIENCE", "JWT_ISSUER", "AuthError", "decode_token"]
