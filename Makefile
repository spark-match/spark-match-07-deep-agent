# =============================================================================
# Spark Match Deep Agent — Makefile
# =============================================================================
# Centralized command center. Each target is a thin wrapper around a
# `uv run ...` invocation. When `make` is unavailable, run the underlying
# command directly (see CLAUDE.md notes).
#
# Conventions:
#   - QA targets: format-fix, lint-fix, format-check, lint-check, typecheck
#   - Test targets: test, test-cov
#   - Run targets: run-local, run-agentcore
#   - Eval targets (future): eval-dev, eval-test
# =============================================================================

# Default goal
.DEFAULT_GOAL := help

# Fail if .env is missing (matches the workshop's safety guard)
ifeq (,$(wildcard .env))
$(error .env file is missing. Run: cp .env.example .env and fill in the missing values)
endif

include .env
export

# uv places its virtualenv at .venv by default
export UV_PROJECT_ENVIRONMENT=.venv
export PYTHONPATH = ./

# QA folders (single source of truth for format/lint)
QA_FOLDERS := src/ tests/

# =============================================================================
# Help
# =============================================================================

.PHONY: help
help: # Show this help message.
	@echo "Spark Match Deep Agent — Make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?# .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?# "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# =============================================================================
# QA — Formatting & Linting
# =============================================================================

.PHONY: format-fix
format-fix: # Auto-format Python code with ruff.
	uv run ruff format $(QA_FOLDERS)

.PHONY: lint-fix
lint-fix: # Auto-fix linting issues with ruff.
	uv run ruff check --fix $(QA_FOLDERS)

.PHONY: format-check
format-check: # Check formatting without modifying files.
	uv run ruff format --check $(QA_FOLDERS)

.PHONY: lint-check
lint-check: # Check linting without fixing.
	uv run ruff check $(QA_FOLDERS)

.PHONY: typecheck
typecheck: # Run mypy strict type checker.
	uv run mypy src/

.PHONY: qa
qa: format-check lint-check typecheck # Run all QA checks (no fix).

.PHONY: qa-fix
qa-fix: format-fix lint-fix # Auto-fix format + lint, then verify with qa.

# =============================================================================
# Tests
# =============================================================================

.PHONY: test
test: # Run pytest (unit tests).
	uv run pytest

.PHONY: test-cov
test-cov: # Run pytest with coverage report.
	uv run pytest --cov=src --cov-report=term-missing

.PHONY: test-verbose
test-verbose: # Run pytest with verbose output.
	uv run pytest -v

# =============================================================================
# Run
# =============================================================================

.PHONY: run-local
run-local: # Run the FastAPI server in local mode (default).
	uv run python -m src

.PHONY: run-agentcore
run-agentcore: # Run the FastAPI server in AgentCore mode.
	SPARK_ENVIRONMENT=agentcore uv run python -m src

.PHONY: run-debug
run-debug: # Run with DEBUG-level logging.
	SPARK_LOG_LEVEL=DEBUG uv run python -m src

# =============================================================================
# Evaluation (future — Sprint 4 per IMPROVEMENTS.md)
# =============================================================================

.PHONY: eval-dev
eval-dev: # Run LLM judge on dev split.
	@echo "TODO: not implemented yet (see IMPROVEMENTS.md §4.7)"

.PHONY: eval-test
eval-test: # Run LLM judge on test split.
	@echo "TODO: not implemented yet (see IMPROVEMENTS.md §4.7)"

# =============================================================================
# Setup
# =============================================================================

.PHONY: install
install: # Install all dependencies (incl. dev extras) with uv.
	uv sync --all-extras

.PHONY: clean
clean: # Remove caches (.pytest_cache, .ruff_cache, .mypy_cache, __pycache__).
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache