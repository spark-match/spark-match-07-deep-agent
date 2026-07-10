"""Application settings with support for local and AgentCore environments.

Reads configuration from environment variables (prefixed with ``SPARK_``) and the
``.env`` file. Settings is cached via :func:`get_settings` so it can be reused
across the application lifecycle without re-parsing.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment."""

    LOCAL = "local"
    AGENTCORE = "agentcore"


class LogLevel(StrEnum):
    """Logging verbosity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


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
    model_provider: str = "bedrock"
    model_id: str = "us.anthropic.claude-sonnet-4-20250514"
    aws_region: str = "us-east-1"

    # --- Agent Configuration ---
    agent_name: str = "spark-match-advisor"
    max_turns: int = 50

    # --- API Server ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:4200"]  # Angular dev server

    # --- Observability ---
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "spark-match-agent"
    langsmith_tracing: bool = False

    # --- Web Search ---
    tavily_api_key: SecretStr | None = None

    @property
    def is_local(self) -> bool:
        return self.environment == Environment.LOCAL

    @property
    def model_string(self) -> str:
        """Build the model string for create_deep_agent."""
        if self.model_provider == "bedrock":
            return f"bedrock:{self.model_id}"
        return f"{self.model_provider}:{self.model_id}"


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
