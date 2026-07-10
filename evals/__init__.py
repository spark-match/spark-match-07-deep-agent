"""LLM-as-judge evaluations for the Spark Match Agent.

This package implements the evaluation framework described in
``IMPROVEMENTS.md`` §4.7. It contains:

- :mod:`evals.dataset` — loads conversation test cases from JSONL.
- :mod:`evals.judge` — Claude-as-judge metric (binary pass/fail).
- :mod:`evals.runner` — orchestrates running cases and judging outputs.

Quick start::

    uv run python -m evals.runner --mode mock      # fast CI smoke test
    uv run python -m evals.runner --mode live      # full eval against the real agent
"""

__all__ = ["dataset", "judge", "runner"]
