"""Assessment subagent — administers RIASEC vocational questionnaire conversationally.

This subagent is delegated by the coordinator when a student needs their
vocational profile evaluated. It conducts the assessment through conversation,
and langmem extracts the RIASEC scores progressively from the student's responses.

The flow:
1. Subagent asks situational questions
2. Student responds naturally
3. langmem extracts/updates StudentProfile in the background
4. When enough data is gathered, subagent calls evaluate_riasec_profile
5. Returns the computed profile to the coordinator

The system prompt is loaded from ``src/prompts/assessment.md`` so prompt
engineering changes show up as diff-friendly Markdown reviews.
"""

from src.prompts import ASSESSMENT_SYSTEM_PROMPT
from src.tools.assessment import evaluate_riasec_profile

ASSESSMENT_SUBAGENT = {
    "name": "assessment",
    "description": (
        "Administra un assessment vocacional RIASEC de forma conversacional. "
        "Hace preguntas situacionales al estudiante, infiere scores para las 6 dimensiones "
        "a partir de sus respuestas, y devuelve el perfil RIASEC calculado "
        "(código de 3 letras + interpretación). El sistema extrae automáticamente "
        "los datos del perfil del estudiante en background usando langmem."
    ),
    "system_prompt": ASSESSMENT_SYSTEM_PROMPT,
    "tools": [evaluate_riasec_profile],
}
