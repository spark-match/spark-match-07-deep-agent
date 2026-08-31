"""System prompts and skills for Spark Match Agent.

Prompts are versioned as ``.md`` files in this directory and loaded via
:mod:`src.prompts.loader`. This package re-exports the canonical names used
by the factory and subagent modules:

- ``SYSTEM_PROMPT`` — coordinator's main system prompt.
- ``ASSESSMENT_SYSTEM_PROMPT`` — assessment subagent.
- ``MATCHING_SYSTEM_PROMPT`` — matching subagent.
- ``PLANNING_SYSTEM_PROMPT`` — planning subagent.
- ``REPORT_SYSTEM_PROMPT`` — report subagent.
- ``reload_prompts()`` — invalidate the loader cache (for tests / admin).
- ``list_prompts()`` — list all available prompts.
- ``USER_MEMORY_SEED`` — template written to ``/memories/AGENTS.md`` the
  first time a user's memory namespace is empty (Sprint 6, task 6.C).
"""

from src.prompts.loader import list_prompts, load_prompt, reload_prompts

SYSTEM_PROMPT = load_prompt("coordinator")
ASSESSMENT_SYSTEM_PROMPT = load_prompt("assessment")
MATCHING_SYSTEM_PROMPT = load_prompt("matching")
PLANNING_SYSTEM_PROMPT = load_prompt("planning")
REPORT_SYSTEM_PROMPT = load_prompt("report")
USER_MEMORY_SEED = load_prompt("user_memory_seed")

__all__ = [
    "ASSESSMENT_SYSTEM_PROMPT",
    "MATCHING_SYSTEM_PROMPT",
    "PLANNING_SYSTEM_PROMPT",
    "REPORT_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "USER_MEMORY_SEED",
    "list_prompts",
    "reload_prompts",
]
