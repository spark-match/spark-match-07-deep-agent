"""Heuristic intent classification for the model router (Sprint 8, task 8.4).

No extra LLM call: classifies the current turn from message-history
heuristics alone (message length, keyword matching, whether the previous
assistant turn looks like a scored assessment question) — the same
"heuristics (length, prior tool calls, keywords), not a classifier LLM"
approach ROADMAP-2026-08.md attributes to the POC v2 router (38% Haiku
coverage measured there with zero extra classification cost).

Design note on the keyword lists below: a naive "short message -> fast"
rule would misclassify short-but-substantive RIASEC narrative (e.g.
"Trabajo como tutor y me siento muy realizado." is only 8 words but is
exactly the kind of nuanced personal-trait statement the assessment
subagent needs the strong model to weigh correctly). ``_NARRATIVE_MARKERS``
exists to keep that content out of the fast lane regardless of length;
only messages that are both short *and* free of narrative markers (plain
greetings, chitchat, short structured queries like "Tengo IAS, ¿qué
carreras me convienen?", low-information replies) are routed to Haiku.
"""

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

# Turns classified into one of these are routed to the fast model
# (IntentRouterMiddleware). Anything else (including the "complex" default
# this module returns for turns that match none of the heuristics) goes to
# the strong model.
FAST_INTENTS = frozenset({"greeting", "chitchat", "assessment_answer", "clarification"})

_GREETING_PREFIXES = (
    "hola",
    "buenas",
    "buenos días",
    "buenos dias",
    "qué tal",
    "que tal",
    "hey",
    "saludos",
)
_GREETING_MAX_WORDS = 6

_CHITCHAT_KEYWORDS = (
    "chiste",
    "cómo estás",
    "como estas",
    "jaja",
    "jeje",
    "adiós",
    "adios",
    "hasta luego",
)

_ASSESSMENT_QUESTION_KEYWORDS = (
    "escala",
    "puntúa",
    "del 1 al 10",
    "riasec",
)

# First-person trait/preference narrative: presence of any of these means
# "treat as complex regardless of length" — this is exactly the content
# the assessment subagent needs careful (strong-model) reasoning to score.
_NARRATIVE_MARKERS = (
    "me gusta",
    "me apasiona",
    "me encanta",
    "me fascina",
    "disfruto",
    "prefiero",
    "soy bueno",
    "soy buena",
    "soy muy",
    "trabajo como",
    "construir",
    "diseñ",
    "arquitect",
    "ingenier",
    "programa",
    "científic",
    "tutor",
    "enseñ",
    "negocio",
    "emprend",
    "organiz",
    "administra",
)

_SHORT_TURN_MAX_WORDS = 8


def _last_human_text(messages: Sequence[BaseMessage]) -> str | None:
    """Return the content of the most recent ``HumanMessage``, if any."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return None


def _previous_ai_message_is_an_assessment_question(messages: Sequence[BaseMessage]) -> bool:
    """Heuristic: did the assistant's last turn look like a scored question?

    Requires interleaved assistant turns (real conversations have them; the
    synthetic eval dataset's cases don't, so this branch is untested by the
    dataset-coverage test below and only exercised directly in unit tests).
    """
    ai_messages = [m for m in messages if isinstance(m, AIMessage)]
    if not ai_messages:
        return False
    content = ai_messages[-1].content
    if not isinstance(content, str):
        return False
    lowered = content.lower()
    return "?" in content and any(kw in lowered for kw in _ASSESSMENT_QUESTION_KEYWORDS)


def classify_intent(messages: Sequence[BaseMessage]) -> str:
    """Classify the current turn's intent from message-history heuristics.

    Returns one of ``FAST_INTENTS`` ("greeting", "chitchat",
    "assessment_answer", "clarification") or "complex" (the default,
    routed to the strong model) when no heuristic matches or the last
    human message can't be read as plain text.
    """
    text = _last_human_text(messages)
    if text is None:
        return "complex"

    stripped = text.strip()
    lowered = stripped.lower()
    word_count = len(stripped.split())

    if word_count <= _GREETING_MAX_WORDS and any(
        lowered.startswith(prefix) for prefix in _GREETING_PREFIXES
    ):
        return "greeting"

    if any(marker in lowered for marker in _NARRATIVE_MARKERS):
        return "complex"

    if any(keyword in lowered for keyword in _CHITCHAT_KEYWORDS):
        return "chitchat"

    if word_count <= _SHORT_TURN_MAX_WORDS:
        if _previous_ai_message_is_an_assessment_question(messages):
            return "assessment_answer"
        return "clarification"

    return "complex"


__all__ = ["FAST_INTENTS", "classify_intent"]
