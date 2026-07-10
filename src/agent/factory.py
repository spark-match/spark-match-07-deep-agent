"""Agent factory - assembles the Spark Match Deep Agent.

Creates the coordinator graph with subagents, memory, and tools.
"""

from collections.abc import Sequence
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from langgraph.graph.state import CompiledStateGraph

from src.agent.middleware import AssessmentOnceMiddleware, MaxTurnsMiddleware
from src.agent.subagents import (
    ASSESSMENT_SUBAGENT,
    MATCHING_SUBAGENT,
    PLANNING_SUBAGENT,
)
from src.config import get_settings
from src.prompts import SYSTEM_PROMPT
from src.tools import (
    calculate_affinity,
    evaluate_riasec_profile,
    search_careers,
    web_search,
)


def create_spark_agent() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Create and configure the Spark Match Deep Agent.

    Returns a compiled LangGraph state graph ready for invocation or streaming.

    Architecture:
    - Coordinator (this agent): routes user intent, manages conversation flow.
    - Assessment subagent: administers the RIASEC questionnaire conversationally.
    - Matching subagent: calculates affinity and ranks careers.
    - Planning subagent: generates personalized action plans.

    Memory:
    - langmem extracts StudentProfile from conversations in the background.
    - Profile persists across sessions via the LangGraph Store.
    - Each subagent has access to the extracted profile context.

    The coordinator decides when to delegate:
    - Quiero descubrir mi perfil -> assessment subagent
    - Que carreras me convienen -> matching subagent
    - Dame un plan para llegar a X -> planning subagent
    - General questions -> coordinator handles directly
    """
    settings = get_settings()

    # SubAgent is a TypedDict; mypy sees plain dict[str, Sequence[object]] from
    # the imported constants, so we cast to satisfy the SubAgent contract.
    subagents: Sequence[SubAgent] = cast(
        "Sequence[SubAgent]",
        [ASSESSMENT_SUBAGENT, MATCHING_SUBAGENT, PLANNING_SUBAGENT],
    )

    agent = create_deep_agent(
        model=settings.model_string,
        tools=[
            evaluate_riasec_profile,
            search_careers,
            calculate_affinity,
            web_search,
        ],
        subagents=subagents,
        system_prompt=SYSTEM_PROMPT,
        name=settings.agent_name,
        middleware=[
            MaxTurnsMiddleware(),
            AssessmentOnceMiddleware(),
        ],
    )

    return agent  # noqa: RET504
