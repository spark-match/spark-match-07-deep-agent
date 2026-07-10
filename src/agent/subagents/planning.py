"""Planning subagent — generates personalized action plans.

This subagent is delegated by the coordinator when a student has chosen
a career direction and needs concrete next steps. It creates structured
plans with skills to develop, courses to take, and milestones to track.

The system prompt is loaded from ``src/prompts/planning.md`` so prompt
engineering changes show up as diff-friendly Markdown reviews.
"""

from src.prompts import PLANNING_SYSTEM_PROMPT
from src.tools.catalog import search_careers
from src.tools.web_search import web_search

PLANNING_SUBAGENT = {
    "name": "planning",
    "description": (
        "Genera planes de acción personalizados para estudiantes que ya eligieron "
        "una dirección profesional. Crea planes estructurados con skills a desarrollar, "
        "cursos recomendados, timeline (3/6/12 meses), y quick wins inmediatos. "
        "Puede buscar en la web cursos reales, certificaciones y recursos actualizados."
    ),
    "system_prompt": PLANNING_SYSTEM_PROMPT,
    "tools": [search_careers, web_search],
}
