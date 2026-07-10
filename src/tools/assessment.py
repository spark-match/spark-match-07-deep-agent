"""Assessment tools — RIASEC profile evaluation."""

from langchain_core.tools import tool


@tool
def evaluate_riasec_profile(
    realistic: int,
    investigative: int,
    artistic: int,
    social: int,
    enterprising: int,
    conventional: int,
) -> dict[str, object]:
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
    scores = {
        "R": realistic,
        "I": investigative,
        "A": artistic,
        "S": social,
        "E": enterprising,
        "C": conventional,
    }

    # Sort by score descending to get dominant types
    sorted_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    dominant_code = "".join([t[0] for t in sorted_types[:3]])

    type_names = {
        "R": "Realista",
        "I": "Investigativo",
        "A": "Artístico",
        "S": "Social",
        "E": "Emprendedor",
        "C": "Convencional",
    }

    return {
        "riasec_code": dominant_code,
        "scores": scores,
        "dominant_types": [
            {"code": t[0], "name": type_names[t[0]], "score": t[1]} for t in sorted_types[:3]
        ],
        "interpretation": (
            f"Tu perfil dominante es {dominant_code} "
            f"({type_names[sorted_types[0][0]]}-"
            f"{type_names[sorted_types[1][0]]}-"
            f"{type_names[sorted_types[2][0]]}). "
            f"Esto indica afinidad con carreras que combinan "
            f"{type_names[sorted_types[0][0]].lower()} y "
            f"{type_names[sorted_types[1][0]].lower()}."
        ),
    }
