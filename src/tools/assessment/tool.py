"""Assessment tool - thin @tool wrapper around the handler.

The wrapper delegates to the handler and unwraps the structured envelope
so the LLM receives the data dict directly (without the {status, data,
errors} wrapper, which would confuse it).
"""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.assessment.handler import evaluate_riasec_profile_handler


@tool
def evaluate_riasec_profile(
    realistic: int,
    investigative: int,
    artistic: int,
    social: int,
    enterprising: int,
    conventional: int,
) -> dict[str, Any]:
    """Evaluate a student's RIASEC vocational profile from assessment scores.

    Each dimension is scored 1-10 based on the student's responses.
    Returns the computed profile with dominant types and interpretation.

    Args:
        realistic: Score for Realistic (hands-on, physical, mechanical)
        investigative: Score for Investigative (analytical, intellectual, scientific)
        artistic: Score for Artistic (creative, expressive, unstructured)
        social: Score for Social (helping, teaching, counseling)
        enterprising: Score for Enterprising (leading, persuading, managing)
        conventional: Score for Conventional (organizing, data, detail-oriented)
    """
    result = evaluate_riasec_profile_handler(
        realistic=realistic,
        investigative=investigative,
        artistic=artistic,
        social=social,
        enterprising=enterprising,
        conventional=conventional,
    )

    # Surface errors to the LLM as a dict so it can recover gracefully.
    if result["status"] == "error":
        return {"error": True, "errors": result["errors"]}

    return cast(dict[str, Any], result["data"])
