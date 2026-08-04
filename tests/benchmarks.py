"""Regression guard for ``docs/benchmarks.md`` -- the document must stay
in sync with what the repo can actually measure.

The document proposes three local measurements that should NOT
regress silently:
1. Handler latency stays under a sane cap (5.2).
2. Mock-mode throughput per case stays under a sane cap (5.3).
3. The bundle's total installed size does not balloon (5.4 -- we add
   one more local metric beyond what the doc lists, because it costs
   nothing to assert and catches dependency drift).

These are CI-friendly smoke checks, not real benchmarks -- the real
benchmarks (latency, cost, helpfulness vs Bedrock) require AWS and
belong to Sprint 11's observability work, per
``docs/benchmarks.md`` SS6.
"""

from __future__ import annotations

import re
import time
from importlib import metadata

MAX_HANDLER_LATENCY_MS = 50.0
MAX_MOCK_CASE_LATENCY_MS = 500.0
MAX_BUNDLE_SIZE_MB = 2000.0  # pyproject.toml pins Python 3.14 with a
# sizeable native dependency tree; the
# real cap is much smaller but this
# guards against accidental regression,
# not the existing baseline.


def test_handler_latency_stays_under_cap() -> None:
    """Handler puro <50ms (Sprint 9, 9.B.4 SS5.1)."""
    from src.tools.assessment.handler import evaluate_riasec_profile_handler

    start = time.perf_counter()
    for _ in range(100):
        evaluate_riasec_profile_handler(
            realistic=5,
            investigative=5,
            artistic=5,
            social=5,
            enterprising=5,
            conventional=5,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000 / 100
    assert elapsed_ms < MAX_HANDLER_LATENCY_MS, (
        f"assessment handler avg latency {elapsed_ms:.2f}ms exceeded "
        f"{MAX_HANDLER_LATENCY_MS}ms cap -- investigate before merging"
    )


def test_mock_mode_throughput_per_case_stays_under_cap() -> None:
    """Mock runner <500ms / case on the 30-case dataset."""
    from evals.runner import run_eval

    start = time.perf_counter()
    results = run_eval(mode="mock")
    elapsed_ms = (time.perf_counter() - start) * 1000

    per_case = elapsed_ms / len(results)
    assert per_case < MAX_MOCK_CASE_LATENCY_MS, (
        f"mock-mode avg per-case latency {per_case:.0f}ms exceeded "
        f"{MAX_MOCK_CASE_LATENCY_MS}ms cap on {len(results)} cases"
    )


def test_bundle_size_does_not_balloon() -> None:
    """Total installed distribution count stays under a sanity cap.

    Counts the number of installed distributions rather than summing
    their on-disk bytes (cross-platform file-size measurement is
    unreliable from ``importlib.metadata``). The point is to catch
    accidental dependency-tree growth (e.g. someone adding a
    multi-megabyte transitive dep without noticing), not to bill the
    bytes -- that belongs in CI artifact analysis, not here.
    """
    distributions = list(metadata.distributions())
    assert distributions, "no installed distributions found -- broken venv?"

    assert len(distributions) < 500, (
        f"installed distribution count {len(distributions)} exceeded 500 -- "
        f"investigate dependency-tree growth before merging"
    )


def test_benchmarks_doc_references_the_measured_sections() -> None:
    """Smoke check: ``docs/benchmarks.md`` exists and mentions both the
    local-measurement sections that this test enforces. Catches the
    case where the doc drifts away from what the repo can actually
    measure."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    doc = (repo_root / "docs" / "benchmarks.md").read_text(encoding="utf-8")
    assert "Handler" in doc or "handler" in doc, (
        "docs/benchmarks.md no longer mentions handler latency"
    )
    assert "mock" in doc.lower(), "docs/benchmarks.md no longer mentions mock-mode throughput"
    # Make sure the doc still warns that mock numbers are not
    # comparable to POC v2 numbers -- otherwise someone might cite them
    # as real benchmark evidence.
    assert re.search(r"no\s+son?\s+comparables", doc, re.IGNORECASE), (
        "docs/benchmarks.md lost the explicit 'not comparable to POC' "
        "warning -- future readers might cite mock numbers as benchmarks"
    )
