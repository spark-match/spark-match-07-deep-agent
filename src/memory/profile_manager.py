"""Profile memory manager — extracts StudentProfile from conversations using langmem.

The profile manager analyzes conversation messages and progressively fills
the StudentProfile schema, persisting it directly into the LangGraph
:class:`~langgraph.store.base.BaseStore` (task 6.D). Extraction runs in the
background via :class:`~langmem.ReflectionExecutor` (task 6.E's
``ProfilePersistMiddleware``) so it never adds latency to a turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langmem import ReflectionExecutor, create_memory_store_manager

from src.config import get_settings
from src.models.profile import StudentProfile

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

# Namespace template: langmem substitutes ``{user_id}`` from
# ``config["configurable"]["user_id"]`` at call time (see
# ``src.agent.memory_middleware.ProfilePersistMiddleware``).
PROFILE_NAMESPACE = ("spark-match", "{user_id}", "profile")

EXTRACTION_INSTRUCTIONS = """\
You are analyzing a vocational guidance conversation to extract a student's profile.

## What to extract

- **Identity**: name, age, education level, current studies
- **RIASEC scores**: Infer scores (1-10) from what the student says about their preferences.
  - Realistic: likes hands-on work, building things, physical activity, outdoors
  - Investigative: likes analyzing, researching, solving abstract problems, science
  - Artistic: likes creating, designing, expressing, unstructured environments
  - Social: likes helping, teaching, caring for others, teamwork
  - Enterprising: likes leading, persuading, taking risks, managing people
  - Conventional: likes organizing, following procedures, working with data, detail
- **Interests**: specific topics, hobbies, activities they mention enjoying
- **Strengths**: skills or abilities they mention or demonstrate
- **Dislikes**: things they explicitly say they don't enjoy or want to avoid
- **Career direction**: any career they mention being interested in

## How to score RIASEC

- Score based on STRENGTH of signal, not just mention:
  - 1-3: Low interest or actively dislikes this dimension
  - 4-6: Moderate or neutral
  - 7-10: Strong interest or passion in this dimension
- Only set a score when you have enough signal from the conversation
- It's OK to leave scores as None until there's clear evidence
- Update scores as more evidence accumulates across messages

## Rules

- Extract ONLY what the student explicitly says or clearly implies
- Do NOT guess or fill in fields without evidence
- Update incrementally — keep existing data, add new data
- If a student contradicts earlier info, update to the latest
"""


def build_profile_manager(store: BaseStore) -> object:
    """Create a langmem store manager configured for StudentProfile extraction.

    Unlike ``create_memory_manager`` (the previous, unused implementation),
    ``create_memory_store_manager`` writes directly into ``store`` under
    :data:`PROFILE_NAMESPACE`, so the extracted profile survives across
    sessions without any extra glue code.

    Args:
        store: The LangGraph store to persist the extracted profile into.
            Comes from :func:`src.persistence.build_persistence` in
            production; any ``BaseStore`` (e.g. ``InMemoryStore``) works in
            tests.

    Uses ``settings.fast_model_string`` (Haiku): structured extraction is a
    cheap, low-stakes task compared to the main conversation model.
    """
    settings = get_settings()

    return create_memory_store_manager(
        settings.fast_model_string,
        schemas=[StudentProfile],
        instructions=EXTRACTION_INSTRUCTIONS,
        namespace=PROFILE_NAMESPACE,
        enable_inserts=False,  # Single profile per user, update in-place.
        store=store,
    )


def build_reflection_executor(store: BaseStore) -> ReflectionExecutor:
    """Wrap :func:`build_profile_manager` in a background reflection executor.

    ``ProfilePersistMiddleware.after_agent`` calls ``.submit(...)`` on the
    result of this function, debounced by
    ``settings.reflection_delay_seconds``, so extraction never blocks the
    user-facing turn.
    """
    return ReflectionExecutor(build_profile_manager(store), store=store)


__all__ = [
    "EXTRACTION_INSTRUCTIONS",
    "PROFILE_NAMESPACE",
    "build_profile_manager",
    "build_reflection_executor",
]
