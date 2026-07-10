"""Profile memory manager — extracts StudentProfile from conversations using langmem.

The profile manager analyzes conversation messages and progressively fills
the StudentProfile schema. It uses langmem's create_memory_manager which:
1. Reads the conversation history
2. Identifies vocational-relevant information
3. Updates the structured profile incrementally
4. Persists across sessions via LangGraph Store
"""

from langmem import create_memory_manager

from src.config import get_settings
from src.models.profile import StudentProfile

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


def create_profile_manager() -> object:
    """Create a langmem memory manager configured for StudentProfile extraction.

    Returns a callable that accepts conversation messages and returns
    the extracted/updated StudentProfile.

    Usage:
        manager = create_profile_manager()
        memories = manager.invoke({"messages": conversation_messages})
        # memories[0].content is a StudentProfile instance
    """
    settings = get_settings()

    manager = create_memory_manager(
        settings.model_string,
        schemas=[StudentProfile],
        instructions=EXTRACTION_INSTRUCTIONS,
        enable_inserts=False,  # Single profile per user, update in-place
    )

    return manager  # noqa: RET504 — kept as a local for test inspection
