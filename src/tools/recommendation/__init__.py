"""Recommendation package - Top-N multicriterio (afinidad + economia + filtros).

El unico sitio donde se define "encaja". Ver `scoring.py` para los pesos y el
porque de cada decision.
"""

from src.tools.recommendation.handler import recommend_programs_handler
from src.tools.recommendation.scoring import (
    REFERENCE_RANGES,
    SCORING_VERSION,
    WEIGHTS,
    score_program,
)
from src.tools.recommendation.tool import recommend_programs

__all__ = [
    "REFERENCE_RANGES",
    "SCORING_VERSION",
    "WEIGHTS",
    "recommend_programs",
    "recommend_programs_handler",
    "score_program",
]
