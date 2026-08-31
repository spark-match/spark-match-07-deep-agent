"""Matching handler - RIASEC affinity calculation (pure business logic).

Pure business logic for computing affinity between a RIASEC profile and
the careers in the catalog. No @tool decorator, no LLM dependencies.

Desde el 2026-08-09 puntua las **554 carreras reales** de
``data/programs/programs.csv`` (via ``load_careers``), no las 20 fichas curadas
de ``data/careers/*.md``, que se retiraron. Ver ADR-019 de
spark-match-03-backend. La formula de similitud no cambia: opera sobre codigos
Holland de tres letras y le da igual de donde salgan.

Structured return schema:
    {
        "status": "success" | "error",
        "data": {"matches": [...], "top_n": int, "riasec_code": str} | None,
        "errors": [<error_message>] | None,
    }
"""

from typing import Any

from src.tools.programs.loader import SOURCE_LABEL, CareerEntry, load_careers

# Positional weights: first letter = most important, third = least.
_POSITION_WEIGHTS: tuple[float, ...] = (3.0, 2.0, 1.0)


def _raw_riasec_score(profile: str, career: str) -> float:
    """Raw (unnormalized) positional-weighted RIASEC match score.

    For each letter in ``profile``, every matching letter in ``career``
    contributes: the same-position bonus (``weight[i] * 10``) if the
    indices align, otherwise the cross-position bonus (``weight[j] * 5``).
    A code with repeated letters can match the same profile position
    multiple times — this is intentional here; it is what makes
    ``_riasec_similarity``'s self-match normalization (see below) always
    an upper bound instead of a fixed constant that degenerate inputs
    could exceed.
    """
    score = 0.0
    for i, letter in enumerate(profile):
        for j, career_letter in enumerate(career):
            if letter == career_letter:
                if i == j:
                    score += _POSITION_WEIGHTS[i] * 10
                else:
                    score += _POSITION_WEIGHTS[j] * 5
    return score


def _riasec_similarity(profile_code: str, career_code: str) -> float:
    """Calculate RIASEC similarity score (0-100).

    Uses positional weighting: first letter = most important.
    Matches in the same position score higher than matches in a different
    position.

    Normalized against the profile's own best-possible match (itself),
    not a fixed constant. For a well-formed RIASEC code (3 distinct
    letters, as produced by ``evaluate_riasec_profile``) self-match always
    equals 60 — the same value the old fixed denominator used — so this
    is a no-op for real input. A malformed/degenerate code with repeated
    letters can score *higher* against itself than a fixed denominator of
    60 allows (verified exhaustively: self-match is the maximum raw score
    achievable against any 3-letter career code, for all 216 possible
    3-letter profile codes over the RIASEC alphabet), which previously
    pushed the reported percentage over 100%. ``min(100.0, ...)`` alone
    would have masked that — every degenerate input would report the same
    100%, indistinguishable from each other — so the denominator itself
    has to scale with the profile, not just the final clamp.
    """
    profile = profile_code[:3]
    career = career_code[:3]
    raw_score = _raw_riasec_score(profile, career)
    self_match = _raw_riasec_score(profile, profile)
    if self_match <= 0:
        return 0.0
    # Belt-and-suspenders clamp for floating-point rounding; mathematically
    # the ratio is already bounded to [0, 1] given self_match's property above.
    return round(min(100.0, (raw_score / self_match) * 100), 1)


# Tope de resultados, por el mismo motivo que en los otros dos handlers: se
# puntuan 554 carreras y devolverlas todas llenaria el contexto del modelo.
MAX_TOP_N = 25


def _score_career(profile_code: str, career: CareerEntry) -> dict[str, Any]:
    """Compute the affinity record for one career."""
    score = _riasec_similarity(profile_code, career["riasec_profile"])
    return {
        "career": career["career"],
        "career_family": career["career_family"],
        "affinity_score": score,
        "riasec_profile": career["riasec_profile"],
        "program_count": career["program_count"],
        "reason": (
            f"Tu perfil {profile_code} tiene {score}% de afinidad con "
            f"{career['career']} (perfil {career['riasec_profile']}). "
            f"Familia: {career['career_family']}. Se estudia en "
            f"{career['program_count']} programa(s) del catalogo."
        ),
    }


def _orden(match: dict[str, Any]) -> tuple[float, int, str]:
    """Afinidad primero; a igualdad, la carrera mas ofertada.

    El desempate importa mucho mas que antes. El catalogo tiene 554 carreras
    repartidas en solo 52 codigos RIASEC distintos, asi que un perfil cualquiera
    empata a 100% con una decena de carreras. Con las 20 fichas de
    `data/careers` los empates eran raros y el orden alfabetico que salia por
    defecto pasaba desapercibido; con 554 ese orden convertiria el top-5 en un
    sorteo. Ordenar los empates por numero de programas pone delante lo que el
    estudiante puede estudiar de verdad en mas sitios, y deja el resultado
    estable entre llamadas.
    """
    return (-match["affinity_score"], -match["program_count"], match["career"])


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
    top_n = min(top_n, MAX_TOP_N)

    catalog = load_careers()
    if not catalog:
        return {
            "status": "error",
            "data": None,
            "errors": ["career catalog is empty - check data/programs/"],
        }

    matches = [_score_career(code, c) for c in catalog]
    matches.sort(key=_orden)

    return {
        "status": "success",
        "data": {
            "matches": matches[:top_n],
            "top_n": top_n,
            "riasec_code": code,
            "total_scored": len(matches),
            "source": SOURCE_LABEL,
        },
        "errors": None,
    }
