"""Resolve the Postgres DSN for the ``postgres`` persistence profile.

Resolution order, mirroring :mod:`src.auth.secret_loader`:

1. ``settings.postgres_dsn`` — local/dev override, used verbatim. Lets the
   evaluator run the postgres profile against a docker Postgres with no AWS
   account at all (hard rule #7 in AGENTS.md). Until now this setting was
   declared but never read.
2. SSM ``db_secret_ssm_param`` -> ARN -> Secrets Manager ``SecretString`` ->
   DSN. This is the production path. The SSM path is the ADR-0002 contract
   (``/spark-match/{env}/config/db-secret-arn``), the same one
   ``spark-match-03-backend`` reads.

The secret holds the JSON that ``modules/rds-postgres`` writes in
spark-match-02-infrastructure:

    {"host", "port", "database", "username", "password"}

Two query parameters are appended and both matter:

``sslmode=require``
    RDS PostgreSQL 15+ rejects unencrypted connections outright.

``options=-csearch_path%3Dagent``
    Pins the session ``search_path`` to the ``agent`` schema so LangGraph's
    checkpoint tables never land in ``public``, where the backend's own
    migrations live. Both services share one database; without this they
    would share one namespace too.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from src.config import get_settings

#: Schema that owns the LangGraph checkpoint/store tables.
AGENT_SCHEMA = "agent"


def _dsn_from_secret(payload: str) -> str:
    """Build a DSN from the Secrets Manager JSON payload."""
    try:
        creds = json.loads(payload)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError("El SecretString de las credenciales de RDS no es JSON valido.") from exc

    missing = [k for k in ("host", "port", "database", "username", "password") if k not in creds]
    if missing:
        raise ValueError(
            f"Al SecretString de RDS le faltan claves: {', '.join(missing)}. "
            "Se espera el shape que escribe modules/rds-postgres."
        )

    user = quote(str(creds["username"]), safe="")
    password = quote(str(creds["password"]), safe="")
    host = creds["host"]
    port = creds["port"]
    database = creds["database"]

    # `options=-csearch_path%3Dagent`: el `=` va percent-encoded porque ya
    # estamos dentro de un valor de query string.
    return (
        f"postgresql://{user}:{password}@{host}:{port}/{database}"
        f"?sslmode=require&options=-csearch_path%3D{AGENT_SCHEMA}"
    )


def _fetch_from_aws() -> str:
    """Resolve the DSN via SSM -> Secrets Manager (production path)."""
    # Import local: mantiene boto3 fuera de cualquier camino que se ejercite
    # sin credenciales AWS en CI (hard rule #7).
    import boto3

    settings = get_settings()
    ssm = boto3.client("ssm", region_name=settings.aws_region)
    arn = ssm.get_parameter(Name=settings.db_secret_ssm_param, WithDecryption=True)["Parameter"][
        "Value"
    ]
    secrets_manager = boto3.client("secretsmanager", region_name=settings.aws_region)
    payload = secrets_manager.get_secret_value(SecretId=arn)["SecretString"]
    return _dsn_from_secret(payload)


def resolve_postgres_dsn() -> str:
    """Return the Postgres DSN for the ``postgres`` persistence profile.

    Not cached: it is read once per process, inside the FastAPI lifespan.
    """
    settings = get_settings()
    if settings.postgres_dsn is not None:
        return settings.postgres_dsn.get_secret_value()
    return _fetch_from_aws()


__all__ = ["AGENT_SCHEMA", "resolve_postgres_dsn"]
