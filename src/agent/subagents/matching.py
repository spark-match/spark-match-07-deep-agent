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
from src.tools.programs import search_programs
from src.tools.recommendation import recommend_programs

MATCHING_SUBAGENT = {
    "name": "matching",
    "description": (
        "Calcula la afinidad entre el perfil RIASEC del estudiante y todas las carreras "
        "del catálogo. Devuelve un ranking Top-5 con scores de afinidad (%) y explicaciones "
        "personalizadas de por qué cada carrera encaja con el perfil, y puede aterrizarlas "
        "en universidades e institutos reales del Perú con su costo, duración y tasa de "
        "admisión."
    ),
    "system_prompt": MATCHING_SYSTEM_PROMPT,
    # `recommend_programs` es la herramienta principal de este subagente: es la
    # única que aplica los filtros del estudiante (región, gestión, tipo de
    # institución, presupuesto) y cruza afinidad con economía en una sola
    # puntuación. Las otras tres siguen porque responden preguntas distintas:
    # `search_careers` qué carreras existen, `search_programs` búsqueda libre
    # sin perfil, `calculate_affinity` afinidad pura sin institución ni cifras.
    "tools": [recommend_programs, search_careers, search_programs, calculate_affinity],
}
