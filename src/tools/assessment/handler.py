"""Assessment handler - RIASEC profile evaluation (pure business logic).

This module contains the pure business logic for evaluating RIASEC profiles.
No @tool decorator, no LLM dependencies - just data in, structured data out.

Structured return schema:
    {
        "status": "success" | "error",
        "data": <riasec_profile_dict> | None,
        "errors": [<error_message>] | None,
    }
"""

from typing import Any, TypedDict


class RiasecScore(TypedDict):
    """A single RIASEC dimension with its score."""

    code: str
    name: str
    score: int


class RiasecProfile(TypedDict):
    """Complete RIASEC profile returned by the handler."""

    riasec_code: str
    scores: dict[str, int]
    dominant_types: list[RiasecScore]
    interpretation: str


# --- Constants ---

_TYPE_NAMES: dict[str, str] = {
    "R": "Realista",
    "I": "Investigativo",
    "A": "ArtÃƒÂ­stico",
    "S": "Social",
    "E": "Emprendedor",
    "C": "Convencional",
}

_VALID_LETTERS = set(_TYPE_NAMES.keys())


# --- Handler (pure business logic) ---


def evaluate_riasec_profile_handler(
    realistic: int,
    investigative: int,
    artistic: int,
    social: int,
    enterprising: int,
    conventional: int,
) -> dict[str, Any]:
    """Evaluate a student's RIASEC vocational profile from assessment scores.

    Pure business logic - no @tool decorator. Testable without LLM.

    Args:
        realistic: Score 1-10 for Realistic (hands-on, physical, mechanical)
        investigative: Score 1-10 for Investigative (analytical, intellectual)
        artistic: Score 1-10 for Artistic (creative, expressive)
        social: Score 1-10 for Social (helping, teaching, counseling)
        enterprising: Score 1-10 for Enterprising (leading, persuading)
        conventional: Score 1-10 for Conventional (organizing, data)

    Returns:
        Structured dict with status, data (the profile), errors.
    """
    scores: dict[str, int] = {
        "R": realistic,
        "I": investigative,
        "A": artistic,
        "S": social,
        "E": enterprising,
        "C": conventional,
    }

    errors: list[str] = []
    for code, value in scores.items():
        if not isinstance(value, int) or not (1 <= value <= 10):
            errors.append(f"score for {code} must be int in [1, 10], got {value!r}")

    if errors:
        return {"status": "error", "data": None, "errors": errors}

    sorted_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    dominant_code = "".join(t[0] for t in sorted_types[:3])

    dominant_types: list[RiasecScore] = [
        {"code": t[0], "name": _TYPE_NAMES[t[0]], "score": t[1]} for t in sorted_types[:3]
    ]

    profile: RiasecProfile = {
        "riasec_code": dominant_code,
        "scores": scores,
        "dominant_types": dominant_types,
        "interpretation": (
            f"Tu perfil dominante es {dominant_code} "
            f"({_TYPE_NAMES[sorted_types[0][0]]}-"
            f"{_TYPE_NAMES[sorted_types[1][0]]}-"
            f"{_TYPE_NAMES[sorted_types[2][0]]}). "
            f"Esto indica afinidad con carreras que combinan "
            f"{_TYPE_NAMES[sorted_types[0][0]].lower()} y "
            f"{_TYPE_NAMES[sorted_types[1][0]].lower()}."
        ),
    }

    return {"status": "success", "data": profile, "errors": None}
