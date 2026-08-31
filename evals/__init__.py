"""LLM-as-judge evaluations for the Spark Match Agent.

This package implements the evaluation framework described in
``IMPROVEMENTS.md`` §4.7. It contains:

- :mod:`evals.dataset` — loads conversation test cases from JSONL.
- :mod:`evals.judge` — Claude-as-judge metric (multi-dimensional weighted
  rubric: ``riasec_accuracy`` 0.4, ``career_relevance`` 0.3, ``tone`` 0.2,
  ``safety`` 0.1, passing at weighted score >= 0.7; default model is
  Haiku 4.5 per POC v2 leccion 4).
- :mod:`evals.runner` — orchestrates running cases and judging outputs.

Quick start::

    uv run python -m evals.runner --mode mock      # fast CI smoke test
    uv run python -m evals.runner --mode live      # full eval against the real agent
"""

__all__ = ["dataset", "judge", "runner"]
