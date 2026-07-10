"""System prompts and skills for Spark Match Agent.

Prompts are versioned as ``.md`` files in this directory and loaded via
:mod:`src.prompts.loader`. This package re-exports the canonical names used
by the factory and subagent modules:

- ``SYSTEM_PROMPT`` — coordinator's main system prompt.
- ``ASSESSMENT_SYSTEM_PROMPT`` — assessment subagent.
- ``MATCHING_SYSTEM_PROMPT`` — matching subagent.
- ``PLANNING_SYSTEM_PROMPT`` — planning subagent.
- ``reload_prompts()`` — invalidate the loader cache (for tests / admin).
- ``list_prompts()`` — list all available prompts.
"""

from src.prompts.loader import list_prompts, load_prompt, reload_prompts

SYSTEM_PROMPT = load_prompt("coordinator")
ASSESSMENT_SYSTEM_PROMPT = load_prompt("assessment")
MATCHING_SYSTEM_PROMPT = load_prompt("matching")
PLANNING_SYSTEM_PROMPT = load_prompt("planning")

__all__ = [
    "ASSESSMENT_SYSTEM_PROMPT",
    "MATCHING_SYSTEM_PROMPT",
    "PLANNING_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "list_prompts",
    "reload_prompts",
]
