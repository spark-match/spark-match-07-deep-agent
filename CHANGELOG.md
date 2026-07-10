# Changelog

All notable changes to the **Spark Match Deep Agent** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed (PR #8 — chore: tests/branch conventions)

- **Test layout convention**: tests now live in subdirectories by domain
  (`tests/<domain>/<module>.py`), with no `test_` filename prefix. The single
  `tests/test_tools.py` (which mixed Assessment/Catalog/Matching tests) was
  split into `tests/tools/{assessment,catalog,matching}.py` — one file per
  source module. `tests/test_logging.py` → `tests/utils/log_setup.py` (renamed
  to avoid stdlib `logging` import clash).
- **pytest discovery**: `[tool.pytest.ini_options]` now sets
  `python_files = ["*.py"]`, `python_classes = ["Test*", "*Tool"]`,
  `python_functions = ["test_*"]` so the new layout is picked up automatically.

### Branch naming convention (going forward)

- `feat/<module>-<short-desc>` for new features
- `fix/<module>-<short-desc>` for bug fixes
- `chore/<aspect>` for housekeeping (tests, deps, tooling)

Examples: `feat/max-turns-middleware`, `fix/career-catalog-encoding`,
`chore/upgrade-ruff`. Branches must be short and reference the module or
aspect touched, not generic tags like `sprint-1` or `feature`.

## [0.2.0] - 2026-07-10 (PR #5 — Sprint 1: higiene operativa)

### Added

- Centralized logging (`src/utils/logging.py`): idempotent `setup_logging()`
  with `SPARK_LOG_LEVEL` env var.
- `Makefile` with 14 targets (QA, test, run, install, clean).
- `.dockerignore` for AgentCore container builds.
- `SecretStr` for `tavily_api_key` and `langsmith_api_key` in `Settings`.
- `LogLevel` StrEnum in `Settings` with strict parsing.
- `CHANGELOG.md` (Keep a Changelog 1.1.0 format).

### Fixed

- `.gitignore` contradiction: `uv.lock` and `.python-version` were both
  tracked AND listed as ignored. Now consistent with `uv` best practices.

## [0.1.0] - 2026-06-25

### Added

- Deep Agent coordinator with 3 subagents: `assessment`, `matching`, `planning`.
- AG-UI streaming endpoint at `POST /ag-ui` (FastAPI + sse-starlette).
- langmem-based `StudentProfile` extraction.
- RIASEC assessment tool with weighted scoring.
- In-memory career catalog with 10 careers.
- Affinity calculator using positional RIASEC weighting.
- Web search tool with Tavily (primary) + DuckDuckGo (fallback).
- Bedrock (Claude Sonnet 4) as default LLM.
- Unit tests for models and tools.
- README with RIASEC model documentation, env config, troubleshooting.

[Unreleased]: https://github.com/spark-match/spark-match-08-deep-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/spark-match/spark-match-08-deep-agent/releases/tag/v0.2.0
[0.1.0]: https://github.com/spark-match/spark-match-08-deep-agent/releases/tag/v0.1.0