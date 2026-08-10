"""Application settings with support for local and AgentCore environments.

Reads configuration from environment variables (prefixed with ``SPARK_``) and the
``.env`` file. Settings is cached via :func:`get_settings` so it can be reused
across the application lifecycle without re-parsing.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment.

    ``dev``/``prod`` son los valores que inyecta la task definition de ECS
    (``SPARK_ENVIRONMENT`` en modules/agent-service de
    spark-match-02-infrastructure). Sin ellos el contenedor no arranca:
    pydantic rechaza el valor y Settings() revienta en el import.

    ``agentcore`` se conserva por compatibilidad con el despliegue anterior.
    """

    LOCAL = "local"
    AGENTCORE = "agentcore"
    DEV = "dev"
    PROD = "prod"


class LogLevel(StrEnum):
    """Logging verbosity levels."""

    # El NOSONAR silencia python:S4507, que avisa de codigo de depuracion
    # entregado en produccion. Falso positivo: esto no enciende ningun modo
    # debug, es el nombre de un nivel de logging del modulo `logging` de la
    # libreria estandar. Quitar el miembro seria quitar un nivel que existe.
    DEBUG = "DEBUG"  # NOSONAR
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PersistenceBackend(StrEnum):
    """Checkpointer/store backend for conversational memory (Sprint 6).

    ``memory`` and ``sqlite`` must work without any AWS credentials (hard
    rule #7 in AGENTS.md — the TFP evaluator runs this repo locally).
    ``postgres`` is production-only and requires Secrets Manager DSN
    resolution (roadmap task 6.A.3), not implemented yet.
    """

    MEMORY = "memory"
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class Settings(BaseSettings):
    """Spark Match Agent settings.

    Reads from environment variables and .env file.
    Switch between local development and AgentCore production
    by changing SPARK_ENVIRONMENT.

    Secret values use :class:`~pydantic.SecretStr` so they are masked in logs
    and tracebacks. Call :meth:`SecretStr.get_secret_value` to read the raw
    string when actually authenticating with the upstream service.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SPARK_",
        case_sensitive=False,
    )

    # --- Environment ---
    environment: Environment = Environment.LOCAL

    # --- Logging ---
    log_level: LogLevel = LogLevel.INFO

    # --- Model Configuration ---
    # model_id must stay within the Bedrock allowlist enforced by IAM in
    # spark-match-02-infrastructure. Only these two inference profiles are
    # permitted; anything else raises AccessDeniedException at call time.
    model_provider: str = "bedrock"
    model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    # Cheaper/faster model for low-stakes routing decisions (Sprint 8 router).
    fast_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    aws_region: str = "us-east-1"
    # POC v2 lesson 9 (Sprint 8, task 8.6): capping generation length
    # reduces latency on plan-generation turns without a quality loss for
    # this agent's response shape (conversational + structured tool
    # output, not long-form essays). Applies to both model/fast_model.
    #
    # 2048 -> 4096: desde la familia 4.6 el thinking adaptativo COMPARTE
    # este presupuesto con el texto de la respuesta, no va aparte. Con 2048
    # una respuesta normal precedida de thinking se corta a media frase y
    # llega con stop_reason=max_tokens. 4096 da margen sin volver a la
    # latencia que la leccion 9 queria evitar.
    max_tokens: int = 4096

    # --- Agent Configuration ---
    agent_name: str = "spark-match-advisor"
    max_turns: int = 50
    # Per-session cap on web_search tool calls (prevents Tavily quota
    # burn on runaway planner loops). Set to 0 to disable the cap.
    max_web_searches_per_session: int = 6

    # --- API Server ---
    api_host: str = "0.0.0.0"
    # Default 8080, not 8000: the frontend service already reserves
    # localhost:8000 (ROADMAP-2026-08.md SS2.5), so the agent container
    # must not collide. The Dockerfile exposes 8080 and sets
    # SPARK_API_PORT=8080 in its ENV. Local dev can still override via
    # the SPARK_API_PORT env var to keep using 8000 if needed.
    api_port: int = 8080
    cors_origins: list[str] = ["http://localhost:4200"]  # Angular dev server

    # --- Observability ---
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "spark-match-agent"
    langsmith_tracing: bool = False

    # --- Web Search ---
    tavily_api_key: SecretStr | None = None

    # --- Persistence (Sprint 6) ---
    # memory/sqlite work fully offline (hard rule #7). postgres is not
    # implemented yet (needs Secrets Manager DSN resolution, task 6.A.3).
    persistence_backend: PersistenceBackend = PersistenceBackend.SQLITE
    sqlite_path: str = ".spark-match/checkpoints.sqlite"
    # Override local del DSN, analogo a jwt_secret: cuando esta seteado se usa
    # verbatim y no se toca AWS, asi el perfil postgres se puede probar contra
    # un Postgres de docker (hard rule #7). Hasta ahora estaba declarado pero
    # no lo leia nadie.
    postgres_dsn: SecretStr | None = None
    # Parametro SSM con el ARN del secret de credenciales de RDS. Contrato
    # ADR-0002, el mismo que lee spark-match-03-backend.
    db_secret_ssm_param: str = "/spark-match/dev/config/db-secret-arn"

    # --- Long-term memory (Sprint 6, tasks 6.D/6.E) ---
    # Delay before the background StudentProfile extraction (langmem
    # ReflectionExecutor) actually runs, debounced from the end of a turn.
    # Keeps the profile manager from re-running on every single message in a
    # fast back-and-forth exchange.
    reflection_delay_seconds: int = 30

    # --- Auth (Sprint 7) ---
    # Local/dev override for the JWT signing secret. When set, this is used
    # verbatim (as raw UTF-8 bytes) instead of resolving SSM -> Secrets
    # Manager, so the evaluator can run and test auth fully offline (hard
    # rule #7). In agentcore/production, leave unset and rely on
    # jwt_secret_ssm_param.
    jwt_secret: SecretStr | None = None
    # SSM parameter holding the ARN of the Secrets Manager secret with the
    # actual signing key. Same path spark-match-03-backend reads from.
    # Ruta del contrato ADR-0002. La anterior (/spark-match/secret/jwt-arn) no
    # existe en ninguna cuenta: el parametro nunca se habria resuelto.
    jwt_secret_ssm_param: str = "/spark-match/dev/config/jwt-secret-arn"
    # How long the resolved secret is cached in-process before re-fetching.
    jwt_secret_cache_seconds: int = 300

    # --- API hardening (Sprint 7, task 7.E) ---
    # Requests per user per minute allowed on POST /ag-ui. The backend
    # (spark-match-03-backend) uses 5 requests / 10 seconds on its login
    # endpoint; this is a coarser per-minute cap on the (costlier) agent
    # invocation endpoint.
    rate_limit_per_minute: int = 5
    # Daily invocation cap per authenticated user_id, enforced against the
    # store (hard rule #4: partitioned by user_id, not the in-process
    # dict/ContextVar counters in src/budget.py, which reset on restart and
    # aren't shared across --workers > 1). Distinct from
    # max_web_searches_per_session, which caps tool calls within a single
    # agent turn rather than requests across a day.
    budget_max_requests_per_user_per_day: int = 200

    # Seconds of silence on the SSE stream before a keep-alive comment is
    # emitted (see src/api/sse.py). Sized against the CloudFront
    # distribution in front of the ALB, whose origin_read_timeout is 60s
    # and cannot go higher without an AWS quota increase: at 15s a stalled
    # turn gets three pings before the proxy would have given up. Set to 0
    # to disable the keep-alive entirely.
    sse_heartbeat_seconds: float = 15.0

    # Whether the AG-UI stream forwards ag_ui_langgraph's RAW passthrough
    # events. Off by default: they carry the verbatim LangGraph internals,
    # system prompts included, to anyone holding a valid JWT. Turn on
    # locally to debug the event stream; never in a deployed environment.
    sse_emit_raw_events: bool = False

    @model_validator(mode="after")
    def _validate_cors_origins(self) -> Settings:
        """Fail fast at startup on an insecure CORS configuration.

        ``CORSMiddleware`` is always registered with ``allow_credentials=True``
        (src/api/app.py), so a wildcard origin would let any website read
        authenticated responses via the browser — the exact case the CORS
        spec forbids and browsers themselves reject. Catching it here turns
        a silent, browser-side no-op into a loud startup failure instead of
        a runtime mystery (task 7.E.1).
        """
        if "*" in self.cors_origins:
            raise ValueError(
                "SPARK_CORS_ORIGINS must not contain '*': CORSMiddleware is "
                "always configured with allow_credentials=True, and browsers "
                "reject wildcard origins combined with credentials. List the "
                "exact origins allowed to call this API instead."
            )
        for origin in self.cors_origins:
            # El "http://" de la linea de abajo no abre ninguna conexion: es el
            # prefijo que se EXIGE a un origen de la lista, y tiene que
            # aceptarse porque en local el frontend corre en http://localhost.
            # De ahi la marca de supresion al final de esa linea -- y aqui no
            # se escribe su nombre, porque Sonar intenta parsear como supresion
            # cualquier comentario donde aparezca, incluido este.
            if not origin.startswith(("http://", "https://")):  # NOSONAR
                raise ValueError(
                    f"SPARK_CORS_ORIGINS entry {origin!r} must start with 'http://' or 'https://'."
                )
        return self

    @property
    def is_local(self) -> bool:
        return self.environment == Environment.LOCAL

    @property
    def model_string(self) -> str:
        """Build the model string for create_deep_agent."""
        if self.model_provider == "bedrock":
            return f"bedrock:{self.model_id}"
        return f"{self.model_provider}:{self.model_id}"

    @property
    def fast_model_string(self) -> str:
        """Build the fast/cheap model string (Haiku) for structured extraction."""
        if self.model_provider == "bedrock":
            return f"bedrock:{self.fast_model_id}"
        return f"{self.model_provider}:{self.fast_model_id}"


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
