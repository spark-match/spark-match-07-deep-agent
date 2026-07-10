# Changelog

All notable changes to the **Spark Match Deep Agent** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Centralized logging** (`src/utils/logging.py`): idempotent `setup_logging()` with configurable level and quiet-down of noisy third-party loggers (`httpx`, `urllib3`). New `SPARK_LOG_LEVEL` env var (DEBUG/INFO/WARNING/ERROR/CRITICAL).
- **`Makefile`** with `help`, `qa`, `qa-fix`, `format-fix`, `lint-fix`, `format-check`, `lint-check`, `typecheck`, `test`, `test-cov`, `run-local`, `run-agentcore`, `run-debug`, `install`, `clean`. Fail-fast if `.env` is missing.
- **`.dockerignore`** for AgentCore container builds (excludes `.venv`, caches, secrets, IDE files, ML artifacts).
- **`SecretStr`** for `tavily_api_key` and `langsmith_api_key` in `Settings`. Web search now calls `get_secret_value()` for upstream auth. Logs and tracebacks no longer risk leaking API keys.
- **`LogLevel` StrEnum** in `Settings` (`log_level` field) with strict parsing.
- **Tests** (`tests/test_logging.py`): 7 cases covering idempotency, level changes, str/int/Logger inputs, third-party quiet-down, message emission.

### Changed

- **`.gitignore`** clarified: `uv.lock` and `.python-version` are explicitly documented as versioned (was previously listed as ignored, contradicting `git ls-files`).
- **`src/api/app.py` lifespan** now calls `setup_logging()` before constructing the agent and logs environment/model on startup/shutdown.

### Fixed

- **`.gitignore` contradiction**: lines 39 and 45 previously ignored `uv.lock` and `.python-version` while both files were tracked. Now consistent with `uv` best practices for applications.

## [0.1.0] - 2026-06-25

### Added

- Deep Agent coordinator with 3 subagents: `assessment`, `matching`, `planning`.
- AG-UI streaming endpoint at `POST /ag-ui` (FastAPI + sse-starlette).
- langmem-based `StudentProfile` extraction (`src/memory/profile_manager.py`).
- RIASEC assessment tool (`src/tools/assessment.py`) with weighted scoring.
- In-memory career catalog with 10 careers (`src/tools/catalog.py`).
- Affinity calculator (`src/tools/matching.py`) using positional RIASEC weighting.
- Web search tool (`src/tools/web_search.py`) with Tavily (primary) + DuckDuckGo (fallback).
- Bedrock (Claude Sonnet 4) as default LLM via `langchain-aws`.
- Unit tests for models and tools (`tests/test_models.py`, `tests/test_tools.py`).
- README with RIASEC model documentation, env config, troubleshooting.

[Unreleased]: https://github.com/spark-match/spark-match-08-deep-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/spark-match/spark-match-08-deep-agent/releases/tag/v0.1.0