"""Affinity matching tool — calculates compatibility between profile and careers."""

from langchain_core.tools import tool

from src.tools.catalog import CAREER_CATALOG


def _riasec_similarity(profile_code: str, career_code: str) -> float:
    """Calculate RIASEC similarity score (0-100).

    Uses positional weighting: first letter = most important.
    Matches in same position score higher than matches in different positions.
    """
    score = 0.0
    weights = [3.0, 2.0, 1.0]  # Weight by position importance

    for i, letter in enumerate(profile_code[:3]):
        for j, career_letter in enumerate(career_code[:3]):
            if letter == career_letter:
                # Same position match scores highest
                if i == j:
                    score += weights[i] * 10
                else:
                    score += weights[j] * 5

    # Normalize to 0-100
    max_possible = sum(w * 10 for w in weights)
    return round((score / max_possible) * 100, 1)


@tool
def calculate_affinity(riasec_code: str, top_n: int = 5) -> list[dict]:
    """Calculate affinity scores between a RIASEC profile and all careers in the catalog.

    Returns the top-N careers sorted by affinity score with explanations.

    Args:
        riasec_code: The student's 3-letter RIASEC code (e.g., 'IAS', 'RIC')
        top_n: Number of top careers to return (default: 5)
    """
    riasec_code = riasec_code.upper()[:3]

    results = []
    for career in CAREER_CATALOG:
        score = _riasec_similarity(riasec_code, career["riasec_profile"])
        results.append(
            {
                "career_id": career["id"],
                "career_name": career["name"],
                "affinity_score": score,
                "career_riasec": career["riasec_profile"],
                "field": career["field"],
                "reason": (
                    f"Tu perfil {riasec_code} tiene {score}% de afinidad con "
                    f"{career['name']} (perfil {career['riasec_profile']}). "
                    f"Campo: {career['field']}."
                ),
            }
        )

    # Sort by score descending
    results.sort(key=lambda x: x["affinity_score"], reverse=True)
    return results[:top_n]
