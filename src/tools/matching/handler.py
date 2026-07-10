"""Matching handler - RIASEC affinity calculation (pure business logic).

Pure business logic for computing affinity between a RIASEC profile and
the careers in the catalog. No @tool decorator, no LLM dependencies.

Structured return schema:
    {
        "status": "success" | "error",
        "data": {"matches": [...], "top_n": int, "riasec_code": str} | None,
        "errors": [<error_message>] | None,
    }
"""

from typing import Any

from src.tools.catalog.loader import Career, load_career_catalog

# Positional weights: first letter = most important, third = least.
_POSITION_WEIGHTS: tuple[float, ...] = (3.0, 2.0, 1.0)


def _riasec_similarity(profile_code: str, career_code: str) -> float:
    """Calculate RIASEC similarity score (0-100).

    Uses positional weighting: first letter = most important.
    Matches in same position score higher than matches in different positions.
    """
    score = 0.0
    profile = profile_code[:3]

    for i, letter in enumerate(profile):
        for j, career_letter in enumerate(career_code[:3]):
            if letter == career_letter:
                # Same position match scores highest
                if i == j:
                    score += _POSITION_WEIGHTS[i] * 10
                else:
                    score += _POSITION_WEIGHTS[j] * 5

    # Normalize to 0-100
    max_possible = sum(w * 10 for w in _POSITION_WEIGHTS)
    return round((score / max_possible) * 100, 1)


def _score_career(profile_code: str, career: Career) -> dict[str, Any]:
    """Compute the affinity record for one career."""
    score = _riasec_similarity(profile_code, career["riasec_profile"])
    return {
        "career_id": career["id"],
        "career_name": career["name"],
        "affinity_score": score,
        "career_riasec": career["riasec_profile"],
        "field": career["field"],
        "reason": (
            f"Tu perfil {profile_code} tiene {score}% de afinidad con "
            f"{career['name']} (perfil {career['riasec_profile']}). "
            f"Campo: {career['field']}."
        ),
    }


def calculate_affinity_handler(riasec_code: str, top_n: int = 5) -> dict[str, Any]:
    """Calculate affinity scores between a RIASEC profile and all careers.

    Pure business logic - no @tool decorator. Testable without LLM.

    Args:
        riasec_code: The student's 3-letter RIASEC code (e.g., 'IAS', 'RIC')
        top_n: Number of top careers to return (default: 5)

    Returns:
        Structured dict with status, data (sorted matches), errors.
    """
    if not isinstance(riasec_code, str) or not riasec_code.strip():
        return {
            "status": "error",
            "data": None,
            "errors": ["riasec_code must be a non-empty string"],
        }

    code = riasec_code.upper()[:3]

    if not top_n or top_n < 1:
        top_n = 1

    catalog = load_career_catalog()
    if not catalog:
        return {
            "status": "error",
            "data": None,
            "errors": ["career catalog is empty - check data/careers/"],
        }

    matches = [_score_career(code, c) for c in catalog]
    matches.sort(key=lambda x: x["affinity_score"], reverse=True)

    return {
        "status": "success",
        "data": {"matches": matches[:top_n], "top_n": top_n, "riasec_code": code},
        "errors": None,
    }
