"""Resolve the JWT signing secret (Sprint 7, task 7.A).

Resolution order:

1. ``settings.jwt_secret`` — local/dev override, used verbatim. Lets the
   evaluator run and test auth fully offline (hard rule #7 in AGENTS.md).
2. SSM ``jwt_secret_ssm_param`` -> ARN -> Secrets Manager ``SecretString``.
   This is the production path, mirroring exactly what
   ``spark-match-03-backend`` reads from (same SSM parameter path).

The secret is cached in-process for ``settings.jwt_secret_cache_seconds``
(default 5 minutes) so a hot request path doesn't hit SSM/Secrets Manager on
every single call.

The signing key MUST be used as raw UTF-8 bytes — no base64-decoding, no
``json.loads`` (hard rule #6). Getting this wrong silently breaks signature
verification for every token.
"""

from __future__ import annotations

from cachetools import TTLCache

from src.config import get_settings

_CACHE: TTLCache[str, bytes] = TTLCache(maxsize=1, ttl=get_settings().jwt_secret_cache_seconds)
_CACHE_KEY = "jwt_secret"


def _fetch_from_aws() -> bytes:
    """Resolve the secret via SSM -> Secrets Manager (production path)."""
    import boto3  # local import: keeps boto3 out of the hot path when the

    # local/dev override is set, and out of any code path exercised without
    # AWS credentials in CI (hard rule #7).

    settings = get_settings()
    ssm = boto3.client("ssm", region_name=settings.aws_region)
    arn = ssm.get_parameter(Name=settings.jwt_secret_ssm_param, WithDecryption=True)["Parameter"][
        "Value"
    ]
    secrets_manager = boto3.client("secretsmanager", region_name=settings.aws_region)
    secret_string = secrets_manager.get_secret_value(SecretId=arn)["SecretString"]
    return secret_string.encode("utf-8")


def load_jwt_secret_sync() -> bytes:
    """Synchronous core of :func:`load_jwt_secret`.

    Exists so callers that can't ``await`` (e.g. ``slowapi``'s ``key_func``,
    which is invoked synchronously — see ``src/api/rate_limit.py``) can
    still resolve the secret. Safe to call from sync code: the AWS path
    uses ``boto3``, which is sync-only regardless.
    """
    if cached := _CACHE.get(_CACHE_KEY):
        return cached

    settings = get_settings()
    if settings.jwt_secret is not None:
        secret = settings.jwt_secret.get_secret_value().encode("utf-8")
    else:
        secret = _fetch_from_aws()

    _CACHE[_CACHE_KEY] = secret
    return secret


async def load_jwt_secret() -> bytes:
    """Return the raw UTF-8 bytes of the JWT signing secret, cached."""
    return load_jwt_secret_sync()


def clear_cache() -> None:
    """Clear the cached secret (tests only)."""
    _CACHE.clear()


__all__ = ["clear_cache", "load_jwt_secret", "load_jwt_secret_sync"]
