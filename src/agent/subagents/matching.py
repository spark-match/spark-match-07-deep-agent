"""Matching subagent — calculates affinity between profile and careers.

This subagent is delegated by the coordinator when a student already has
a RIASEC profile and needs career recommendations. It searches the catalog,
calculates affinity scores, and presents a ranked list with explanations.

The system prompt is loaded from ``src/prompts/matching.md`` so prompt
engineering changes show up as diff-friendly Markdown reviews.
"""

from src.prompts import MATCHING_SYSTEM_PROMPT
from src.tools.catalog import search_careers
from src.tools.matching import calculate_affinity

MATCHING_SUBAGENT = {
    "name": "matching",
    "description": (
        "Calcula la afinidad entre el perfil RIASEC del estudiante y todas las carreras "
        "del catálogo. Devuelve un ranking Top-5 con scores de afinidad (%) y explicaciones "
        "personalizadas de por qué cada carrera encaja con el perfil."
    ),
    "system_prompt": MATCHING_SYSTEM_PROMPT,
    "tools": [search_careers, calculate_affinity],
}
