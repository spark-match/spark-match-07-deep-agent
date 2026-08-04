"""Evaluation runner - executes the agent on each dataset case and judges outputs.

Two modes:
- ``--mock``: skip the real agent, use the handler directly. Fast for CI.
- ``--live``: invoke the real LangGraph agent. Requires AWS credentials.

Both modes produce a pass-rate report in the console and (optionally) JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class CaseResult:
    """Result of running one eval case."""

    case_id: str
    scenario: str
    passed: bool
    reason: str
    output: str = ""


# Keyword -> RIASEC dimension heuristic. A deliberately weak, rule-based
# substitute for an LLM interpreting free text, used only in mock mode
# (--mode mock). Maps keyword hits in the conversation to a boost for the
# corresponding dimension; see _derive_dimension_scores.
_DIMENSION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "R": ("manos", "construir", "físic", "práctic", "ingenier", "carretera", "puente", "mecánic"),
    "I": (
        "programa",
        "comput",
        "lógic",
        "científic",
        "experiment",
        "analiz",
        "resuelvo",
        "investiga",
    ),
    "A": ("arte", "diseñ", "creativ", "ilustra", "estétic", "arquitect", "visual", "gráfic"),
    "S": ("enseñ", "tutor", "ayudar", "social", "comunic", "aprend", "realizado"),
    "E": ("emprend", "líder", "lider", "negocio", "venta", "gestion", "persuad"),
    "C": ("organiz", "detall", "orden", "administra", "sistemátic", "datos"),
}

_RIASEC_CODE_RE = re.compile(r"\b([RIASEC]{3})\b")


def _derive_dimension_scores(text: str) -> dict[str, int]:
    """Count keyword hits per RIASEC dimension from free text.

    Returns scores in the [1, 10] range required by
    ``evaluate_riasec_profile_handler`` — a baseline of 1 (never zero,
    which the handler rejects) plus 3 points per keyword hit, capped at
    10. This is intentionally imprecise: it drives the *real* handler
    with *some* differentiated (non-tied) input derived from the actual
    conversation, instead of hand-picking the expected answer.
    """
    lowered = text.lower()
    return {
        dim: min(10, 1 + 3 * sum(1 for kw in keywords if kw in lowered))
        for dim, keywords in _DIMENSION_KEYWORDS.items()
    }


def _format_expected(case) -> str:
    """Render the expected behavior as a one-line string for the judge."""
    parts: list[str] = []
    if case.expected_riasec:
        parts.append(f"riasec={case.expected_riasec}")
    if case.expected_careers_count is not None:
        parts.append(f"careers_count={case.expected_careers_count}")
    if case.expected_career_id:
        parts.append(f"career_id={case.expected_career_id}")
    if case.expected_status:
        parts.append(f"status={case.expected_status}")
    if case.expected_no_tool_calls:
        parts.append("no_tool_calls")
    if case.expected_invokes_assessment:
        parts.append("invokes_assessment")
    return ", ".join(parts) or "any reasonable response"


def _run_mock_case(case) -> str:
    """Run a case using the pure handlers (no LLM) — never the expected answer.

    Useful for CI smoke tests - no AWS credentials needed.

    Drives the real handlers with input derived from the case's own
    conversation text, not from ``case.expected_*``:

    - Matching-type cases (``expected_careers_count`` set): the simulated
      user states their RIASEC code explicitly in the conversation (e.g.
      "Tengo IAS"), so it is extracted from that text with a regex.
    - Assessment-type cases (``expected_riasec`` set, no career count):
      per-dimension scores are derived from a keyword heuristic (see
      ``_derive_dimension_scores``) and fed to the real
      ``evaluate_riasec_profile_handler`` — the returned code is whatever
      *that* computes, which may legitimately differ from
      ``expected_riasec`` (see ``_mock_evaluate``'s overlap check).
    """
    from src.tools.assessment.handler import evaluate_riasec_profile_handler
    from src.tools.matching.handler import calculate_affinity_handler

    user_text = " ".join(t.content for t in case.turns if t.role == "user")

    if case.expected_careers_count is not None:
        match = _RIASEC_CODE_RE.search(user_text.upper())
        riasec = match.group(1) if match else "IRC"
        result = calculate_affinity_handler(riasec_code=riasec, top_n=case.expected_careers_count)
        matches = result["data"]["matches"]
        return f"Para tu perfil {riasec}, las carreras más afines son:\n" + json.dumps(
            matches, ensure_ascii=False
        )

    if case.expected_riasec:
        scores = _derive_dimension_scores(user_text)
        result = evaluate_riasec_profile_handler(
            realistic=scores["R"],
            investigative=scores["I"],
            artistic=scores["A"],
            social=scores["S"],
            enterprising=scores["E"],
            conventional=scores["C"],
        )
        riasec = result["data"]["riasec_code"]
        return f"Perfil detectado: {riasec}. " + result["data"]["interpretation"]

    return f"Respuesta simulada para el caso {case.id}"


def _run_live_case(case) -> str:
    """Run a case using the real LangGraph agent. Requires AWS credentials."""
    from src.agent.factory import create_spark_agent
    from src.budget import reset_session_budget

    agent = create_spark_agent()
    messages = [{"role": t.role, "content": t.content} for t in case.turns]

    reset_session_budget(case.id)

    result = agent.invoke(
        {"messages": messages},
        config={"configurable": {"thread_id": case.id}},
    )

    # Extract last AI message
    final_messages = result.get("messages", [])
    if final_messages:
        return str(final_messages[-1].content)
    return "(no messages)"


def run_eval(mode: str = "mock") -> list[CaseResult]:
    """Run the full evaluation dataset.

    Args:
        mode: "mock" (no LLM, fast) or "live" (real agent)

    Returns:
        List of CaseResult with pass/fail status and judge reason.
    """
    from evals.dataset import load_dataset

    cases = load_dataset()
    # In mock mode, skip the LLM judge - just check that output is non-empty
    # and matches expected heuristics. This makes mock mode truly offline.
    judge = None if mode == "mock" else _build_judge()
    results: list[CaseResult] = []

    for case in cases:
        output = _run_mock_case(case) if mode == "mock" else _run_live_case(case)

        expected_str = _format_expected(case)
        scenario = f"{case.scenario} (id={case.id})"

        if judge is None:
            # Mock mode: heuristic check (no LLM)
            passed, reason = _mock_evaluate(case, output)
        else:
            try:
                score = judge.score(
                    output=output,
                    expected=expected_str,
                    scenario=scenario,
                )
                passed = score.value >= 0.5
                reason = score.reason
            except Exception as exc:
                passed = False
                reason = f"FAIL: judge error: {exc}"

        results.append(
            CaseResult(
                case_id=case.id,
                scenario=case.scenario,
                passed=passed,
                reason=reason,
                output=output[:500],
            )
        )

    return results


def _build_judge():
    """Build the LLM judge (lazy import)."""
    from evals.judge import SparkMatchJudge

    return SparkMatchJudge()


def _mock_evaluate(case, output: str) -> tuple[bool, str]:
    """Heuristic check used in mock mode (no LLM).

    Checks:
    - Output is non-empty
    - For matching cases, the RIASEC code was extracted from the user's
      own explicit statement (see ``_run_mock_case``), so an exact match
      is expected — this is not a heuristic guess.
    - For assessment cases, the code came from a keyword heuristic
      driving the real handler (see ``_derive_dimension_scores``); an
      exact 3-letter match is not guaranteed from ~2 sentences of free
      text, so this requires only that at least 2 of the 3 expected
      letters appear (order-independent) — still a real bar the
      heuristic can fail, unlike the previous verbatim
      ``expected_riasec in output`` check (B9: that could never fail,
      since the mock case built the output by embedding
      ``expected_riasec`` directly).
    - For matching cases, output contains career names.
    - For chitchat/redirect, output does not contain a RIASEC code.
    """
    if not output or not output.strip():
        return False, "FAIL: empty output"

    output_upper = output.upper()

    if case.expected_riasec:
        detected = _RIASEC_CODE_RE.search(output_upper)
        if not detected:
            return (
                False,
                f"mock FAIL: no RIASEC code found in output (expected {case.expected_riasec})",
            )
        detected_code = detected.group(1)
        expected_upper = case.expected_riasec.upper()

        if case.expected_careers_count is not None:
            # Matching-type: the code was extracted from explicit user
            # text, not guessed — exact match is the right bar.
            if detected_code == expected_upper:
                return True, f"mock PASS: extracted code {detected_code} matches expected"
            return False, f"mock FAIL: extracted {detected_code}, expected {expected_upper}"

        overlap = set(detected_code) & set(expected_upper)
        if len(overlap) >= 2:
            return (
                True,
                f"mock PASS: heuristic detected {detected_code}, shares "
                f"{len(overlap)}/3 letters with expected {expected_upper}",
            )
        return (
            False,
            f"mock FAIL: heuristic detected {detected_code}, shares only "
            f"{len(overlap)}/3 letters with expected {expected_upper}",
        )

    if case.expected_career_id:
        if case.expected_career_id.lower() in output.lower():
            return True, f"mock PASS: output mentions career {case.expected_career_id}"
        return False, f"mock FAIL: output missing career {case.expected_career_id}"

    if case.expected_no_tool_calls:
        # In mock mode there's no real LLM to evaluate, so we just check
        # the output doesn't claim to call specific tools.
        if "RIASEC" in output_upper or "@tool" in output:
            return False, "mock FAIL: output looks like a tool invocation"
        return True, "mock PASS: output does not invoke tools"

    return True, "mock PASS: non-empty output"


def print_report(results: list[CaseResult]) -> None:
    """Print a pass/fail report to stdout."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pct = (passed / total * 100) if total else 0.0

    print("=" * 72)
    print(f"Eval Report: {passed}/{total} passed ({pct:.0f}%)")
    print("=" * 72)

    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        print(f"[{marker}] {r.case_id} ({r.scenario}): {r.reason}")

    print()
    if passed < total:
        print(f"[WARN] {total - passed} cases failed")
        sys.exit(1)
    print("[OK] All cases passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Spark Match evals")
    parser.add_argument(
        "--mode",
        choices=["mock", "live"],
        default="mock",
        help="mock = no LLM, fast; live = real LangGraph agent (needs AWS creds)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional path to write JSON results",
    )
    args = parser.parse_args()

    results = run_eval(mode=args.mode)

    if args.json:
        args.json.write_text(
            json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print_report(results)


if __name__ == "__main__":
    main()
