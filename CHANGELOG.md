# Changelog

All notable changes to the **Spark Match Deep Agent** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]


### Added (PR #7 â€” Sprint 3)

- **Â§4.1 â€” LangSmith observability**: new `src/observability/langsmith.py` with `configure_langsmith()` (idempotent) and `is_langsmith_enabled()`. Pushes `SPARK_LANGSMITH_*` settings into the `LANGSMITH_*` env vars that `langchain-aws` / `deepagents` read automatically. `app.py` lifespan calls it on startup so traces flow to the configured project. Trivially opt-in: set `SPARK_LANGSMITH_TRACING=true` + `SPARK_LANGSMITH_API_KEY` in `.env`.
- **Â§4.6 â€” Markdown prompts**: moved the 4 system prompts (coordinator, assessment, matching, planning) from inline Python strings to `src/prompts/*.md` files with YAML frontmatter (`audience`, `loaded_by`, `versioned`). New `src/prompts/loader.py` with `load_prompt(name)`, `get_prompt_metadata(name)`, `list_prompts()`, `reload_prompts()`. Subagent modules import from `src.prompts` instead of defining the prompt themselves. Removed `src/prompts/system.py`.
- **Â§5.1 â€” Expanded lint config**: ruff lint rules now include `ASYNC`, `PT`, `RET`, `RUF` (was just `E`, `F`, `I`, `UP`, `B`, `SIM`). New `per-file-ignores` allow `S101` (assert) and `PT004` (fixture returns) in tests. mypy now has `disallow_untyped_defs`, `no_implicit_optional`, `warn_redundant_casts`, `warn_unused_ignores` on top of `strict = true`. Consolidated `[project.optional-dependencies]` into `[dependency-groups]` dev group (uv 0.5+ idiom).
- **15 tests in `tests/test_prompts.py`**: loader on real `.md` files, metadata parsing, re-export consistency, frontmatter edge cases.
- **5 tests in `tests/test_langsmith.py`**: env-var push, idempotency, masking regression test for `SecretStr`.

### Changed

- `src/agent/subagents/{assessment,matching,planning}.py` are now thin wrappers: just import the prompt and define the `SubAgent` dict. Prompt engineering is no longer mixed with code logic.
- `src/prompts/__init__.py` re-exports `SYSTEM_PROMPT`, `ASSESSMENT_SYSTEM_PROMPT`, `MATCHING_SYSTEM_PROMPT`, `PLANNING_SYSTEM_PROMPT`, `list_prompts`, `reload_prompts`.
- `src/api/app.py` lifespan now calls `configure_langsmith()` after `setup_logging()` and before agent construction.

## [0.3.0] - 2026-07-10 (PR #6 â€” Sprint 2)

### Added (PR #6 â€” Sprint 2 + correcciones pre-existentes)


### Added (PR #6 Ã¢â‚¬â€ Sprint 2 + correcciones pre-existentes)

- **FIX-1 Ã¢â‚¬â€ AG-UI endpoint migration**: replaced deprecated `create_endpoint(agent)` with `LangGraphAgent` wrapper + manual `/ag-ui` endpoint that uses `EventEncoder` + `RunAgentInput`. The new endpoint reads `thread_id` from the request body and uses it as the active session for budget tracking.
- **FIX-2 Ã¢â‚¬â€ mypy strict clean**: resolved 16 pre-existing mypy errors (was 18 Ã¢â€ â€™ now 0):
  - Added `Career` and `AffinityResult` TypedDicts in `src/tools/catalog.py` and `src/tools/matching.py` to replace generic `dict[str, object]` / `list[dict]`.
  - Annotated `create_spark_agent()` return type as `CompiledStateGraph[Any, Any, Any, Any]`.
  - Added cast in `factory.py` to satisfy the `SubAgent` TypedDict contract.
  - Added return type annotation `-> object` on `create_profile_manager()`.
  - Added `[[tool.mypy.overrides]]` for `ag_ui_langgraph*`, `tavily*`, `langmem*`, `deepagents*` (lacking upstream py.typed markers).
  - Installed `types-PyYAML` dev dependency.
  - Fixed `StreamHandler` argument type in `src/utils/logging.py`.
- **Ã‚Â§3.4 Ã¢â‚¬â€ Decoupled career catalog**: 10 careers moved from hard-coded Python `list[dict]` to Markdown files under `data/careers/` with YAML frontmatter. Added `Career` TypedDict, `load_career_catalog()`, `reload_career_catalog()`, `CATALOG_DIR` resolver. New `pyyaml>=6.0.3` dependency. Career body (description, resources) is now preserved for the planning subagent.
- **Ã‚Â§4.2 Ã¢â‚¬â€ Web search budget guard**: per-session counter (`src/budget.py`) using `ContextVar` for active session. `web_search` tool now refuses calls beyond `settings.max_web_searches_per_session` (default 6) and returns a clear "budget exhausted" message instead of burning the Tavily quota. AG-UI endpoint calls `set_active_session(thread_id)` + `reset_session_budget(thread_id)` per request.
- **Ã‚Â§5.3 Ã¢â‚¬â€ `__init__.py` re-exports**: top-level `src/__init__.py` now exports `create_spark_agent`, `Settings`, `get_settings`. `src/agent/__init__.py` and `src/config/__init__.py` have explicit re-exports for callers that prefer a flat import surface.
- **11 new tests in `tests/test_catalog.py`**: catalog loader on real `data/careers/` (count, uniqueness, RIASEC validity), parser edge cases (missing/malformed/incomplete frontmatter), cache invalidation.
- **6 new tests in `tests/test_budget.py`**: per-session counter, session isolation, active session context, default session id.
- **1 new test in `tests/test_tools.py`**: web search budget guard end-to-end.
- **Settings** gained `max_web_searches_per_session: int = 6` (env var `SPARK_MAX_WEB_SEARCHES_PER_SESSION`). Existing `max_turns` documented but enforcement deferred to Sprint 4 (post-model middleware).

### Changed

- **`src/tools/web_search.py`**: increments budget counter BEFORE performing the search (prevents retry storms on transient errors).
- **`src/tools/catalog.py`**: `search_careers` now also matches against `outlook` and `body` (description/resources), not just name/skills/field.
- **`src/api/app.py`**: registers `/ag-ui` and `/ag-ui/health` routes manually (not via `add_langgraph_fastapi_endpoint`) so we can hook the per-request budget reset.
- **`.env.example`**: added `SPARK_MAX_WEB_SEARCHES_PER_SESSION=6` and `SPARK_MAX_TURNS=50`.

### Fixed

- **AG-UI import error**: `from ag_ui_langgraph import create_endpoint` failed on `ag-ui-langgraph` 0.0.42 (the symbol was renamed). Now uses `LangGraphAgent` + manual endpoint following the new SDK API.
- **Type annotations**: `dict` Ã¢â€ â€™ `dict[str, str]` / `dict[str, object]` where appropriate; `list[dict]` Ã¢â€ â€™ `list[Career]` / `list[AffinityResult]`.
- **Sub-agent type mismatch**: factory now casts to `Sequence[SubAgent]` to satisfy deepagents' TypedDict contract.

## [0.2.0] - 2026-07-10 (PR #5 Ã¢â‚¬â€ Sprint 1)


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

[0.2.0]: https://github.com/spark-match/spark-match-08-deep-agent/compare/v0.1.0...v0.2.0

[0.1.0]: https://github.com/spark-match/spark-match-08-deep-agent/releases/tag/v0.1.0