# Changelog

All notable changes to the **Spark Match Deep Agent** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added (Sprint 4)

- **Section 4.5 - Handler/tool refactor**: each tool now lives in a dedicated
  package (`src/tools/<name>/`) split into:
  - `handler.py` - pure business logic with no `@tool` decorator, no LLM
    dependency. Returns a structured envelope `{"status", "data", "errors"}`
    for predictable testing.
  - `tool.py` - thin `@tool` wrapper that delegates to the handler and unwraps
    the envelope for the LLM.
  - `__init__.py` - re-exports.
  Affected packages: `assessment`, `catalog`, `matching`, `web_search`. The
  handler functions are now reusable from CLI / API / tests without
  instantiating the LangGraph runtime. The catalog loader was extracted from
  the (now-deleted) `src/tools/catalog.py` into `src/tools/catalog/loader.py`
  for clean separation.
- **Section 4.7 - LLM-as-judge evaluation framework**: new `evals/` package
  with:
  - `evals/dataset.jsonl` - 10 sample conversations covering assessment,
    matching, planning, chitchat, redirect, and ambiguous-profile scenarios.
  - `evals/dataset.py` - JSONL loader returning typed `EvalCase` records.
  - `evals/judge.py` - `SparkMatchJudge` (Claude via Bedrock) that scores
    one agent output against the expected behavior with a binary pass/fail
    JSON response.
  - `evals/runner.py` - CLI runner with `--mode mock` (heuristic, no AWS
    creds needed) and `--mode live` (real LangGraph agent). Mock mode is used
    in CI.
- **CI workflow** (`.github/workflows/ci.yml`): pytest + ruff check + ruff
  format check + mypy strict + eval mock mode on every push to `main` / `dev`
  and on every PR. Uses `uv sync --all-groups` and a cached `.venv`.

### Added (PR #7 - Sprint 3)

- **Section 4.1 - LangSmith observability**: new `src/observability/langsmith.py`
  with `configure_langsmith()` (idempotent) and `is_langsmith_enabled()`.
  Pushes `SPARK_LANGSMITH_*` settings into the `LANGSMITH_*` env vars that
  `langchain-aws` / `deepagents` read automatically. `app.py` lifespan calls
  it on startup so traces flow to the configured project. Trivially opt-in:
  set `SPARK_LANGSMITH_TRACING=true` + `SPARK_LANGSMITH_API_KEY` in `.env`.
- **Section 4.6 - Markdown prompts**: moved the 4 system prompts (coordinator,
  assessment, matching, planning) from inline Python strings to
  `src/prompts/*.md` files with YAML frontmatter (`audience`, `loaded_by`,
  `versioned`). New `src/prompts/loader.py` with `load_prompt(name)`,
  `get_prompt_metadata(name)`, `list_prompts()`, `reload_prompts()`. Subagent
  modules import from `src.prompts` instead of defining the prompt
  themselves. Removed `src/prompts/system.py`.
- **Section 5.1 - Expanded lint config**: ruff lint rules now include `ASYNC`,
  `PT`, `RET`, `RUF` (was just `E`, `F`, `I`, `UP`, `B`, `SIM`). New
  `per-file-ignores` allow `S101` (assert) and `PT004` (fixture returns) in
  tests. mypy now has `disallow_untyped_defs`, `no_implicit_optional`,
  `warn_redundant_casts`, `warn_unused_ignores` on top of `strict = true`.
  Consolidated `[project.optional-dependencies]` into `[dependency-groups]`
  dev group (uv 0.5+ idiom).
- **15 tests in `tests/test_prompts.py`**: loader on real `.md` files,
  metadata parsing, re-export consistency, frontmatter edge cases.
- **5 tests in `tests/test_langsmith.py`**: env-var push, idempotency, masking
  regression test for `SecretStr`.

### Changed

- `src/agent/subagents/{assessment,matching,planning}.py` are now thin
  wrappers: just import the prompt and define the `SubAgent` dict. Prompt
  engineering is no longer mixed with code logic.
- `src/prompts/__init__.py` re-exports `SYSTEM_PROMPT`, `ASSESSMENT_SYSTEM_PROMPT`,
  `MATCHING_SYSTEM_PROMPT`, `PLANNING_SYSTEM_PROMPT`, `list_prompts`,
  `reload_prompts`.
- `src/api/app.py` lifespan now calls `configure_langsmith()` after
  `setup_logging()` and before agent construction.

## [0.3.0] - 2026-07-10 (PR #6 - Sprint 2)

### Added (PR #6 - Sprint 2 + correcciones pre-existentes)

- **FIX-1 - AG-UI endpoint migration**: replaced deprecated
  `create_endpoint(agent)` with `LangGraphAgent` wrapper + manual `/ag-ui`
  endpoint that uses `EventEncoder` + `RunAgentInput`. The new endpoint reads
  `thread_id` from the request body and uses it as the active session for
  budget tracking.
- **FIX-2 - mypy strict clean**: resolved 16 pre-existing mypy errors
  (was 18, now 0):
  - Added `Career` and `AffinityResult` TypedDicts in `src/tools/catalog.py`
    and `src/tools/matching.py` to replace generic `dict[str, object]` /
    `list[dict]`.
  - Annotated `create_spark_agent()` return type as
    `CompiledStateGraph[Any, Any, Any, Any]`.
  - Added cast in `factory.py` to satisfy the `SubAgent` TypedDict contract.
  - Added return type annotation `-> object` on `create_profile_manager()`.
  - Added `[[tool.mypy.overrides]]` for `ag_ui_langgraph*`, `tavily*`,
    `langmem*`, `deepagents*` (lacking upstream py.typed markers).
  - Installed `types-PyYAML` dev dependency.
  - Fixed `StreamHandler` argument type in `src/utils/logging.py`.
- **Section 3.4 - Decoupled career catalog**: 10 careers moved from hard-coded
  Python `list[dict]` to Markdown files under `data/careers/` with YAML
  frontmatter. Added `Career` TypedDict, `load_career_catalog()`,
  `reload_career_catalog()`, `CATALOG_DIR` resolver. New `pyyaml>=6.0.3`
  dependency. Career body (description, resources) is now preserved for the
  planning subagent.
- **Section 4.2 - Web search budget guard**: per-session counter
  (`src/budget.py`) using `ContextVar` for active session. `web_search` tool
  now refuses calls beyond `settings.max_web_searches_per_session` (default 6)
  and returns a clear "budget exhausted" message instead of burning the Tavily
  quota. AG-UI endpoint calls `set_active_session(thread_id)` +
  `reset_session_budget(thread_id)` per request.
- **Section 5.3 - `__init__.py` re-exports**: top-level `src/__init__.py`
  now exports `create_spark_agent`, `Settings`, `get_settings`.
  `src/agent/__init__.py` and `src/config/__init__.py` have explicit
  re-exports for callers that prefer a flat import surface.
- **11 new tests in `tests/test_catalog.py`**: catalog loader on real
  `data/careers/` (count, uniqueness, RIASEC validity), parser edge cases
  (missing/malformed/incomplete frontmatter), cache invalidation.
- **6 new tests in `tests/test_budget.py`**: per-session counter, session
  isolation, active session context, default session id.
- **1 new test in `tests/test_tools.py`**: web search budget guard end-to-end.
- **Settings** gained `max_web_searches_per_session: int = 6` (env var
  `SPARK_MAX_WEB_SEARCHES_PER_SESSION`). Existing `max_turns` documented but
  enforcement deferred to Sprint 4 (post-model middleware).

### Changed

- **`src/tools/web_search.py`**: increments budget counter BEFORE performing
  the search (prevents retry storms on transient errors).

## [0.2.0] - 2026-07-09

### Added

- RIASEC assessment, career matching, planning subagents.
- AG-UI streaming endpoint.
- langmem profile extraction.
- Tools: `evaluate_riasec_profile`, `search_careers`, `calculate_affinity`,
  `web_search` (Tavily + DuckDuckGo fallback).

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

[Unreleased]: https://github.com/spark-match/spark-match-08-deep-agent/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/spark-match/spark-match-08-deep-agent/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/spark-match/spark-match-08-deep-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/spark-match/spark-match-08-deep-agent/releases/tag/v0.1.0