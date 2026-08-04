"""MCP server exposing the Spark Match agent's tools (Sprint 8, task 8.5).

Scope decision: exposes only. This module lets other MCP clients (Claude
Desktop, other agents, etc.) call this project's 4 tools
(``evaluate_riasec_profile``, ``search_careers``, ``calculate_affinity``,
``web_search``) over the Model Context Protocol. It does **not** consume
external MCP servers -- that's the other half of the roadmap's task 8.5
and was explicitly scoped out for this PR (see ``.mcp.json`` / the PR
description for the reasoning).

Registers the pure *handlers* (``src/tools/*/handler.py``), not the
LangChain-wrapped ``@tool`` objects from ``src/tools/*/tool.py``:
``MCPServer.tool()`` introspects a plain Python function's signature and
docstring directly -- the same shape LangChain's ``@tool`` wraps -- so
reusing the handler keeps this module a thin delegator, consistent with
the handler/tool separation convention (AGENTS.md §6): no business logic
here, and the *same* business logic backing both the LangChain tools and
this MCP server (one implementation, two protocol adapters).

Run standalone (stdio transport, for local MCP clients like Claude
Desktop -- see ``.mcp.json`` at the repo root):

.. code-block:: shell

    uv run python -m src.mcp
"""

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from src.tools.assessment.handler import evaluate_riasec_profile_handler
from src.tools.catalog.handler import search_careers_handler
from src.tools.matching.handler import calculate_affinity_handler
from src.tools.web_search.handler import web_search_handler

mcp_server: MCPServer = MCPServer(
    name="spark-match-agent",
    instructions=(
        "Spark Match's vocational-guidance tools: RIASEC assessment "
        "scoring, career catalog search, career-affinity matching, and "
        "web search for current career/education information."
    ),
)


def _unwrap_or_raise(result: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the handler's ``{status, data, errors}`` envelope for MCP callers.

    Mirrors ``src/tools/*/tool.py``'s LangChain wrappers, adapted to MCP's
    own error convention: raise :class:`ToolError` instead of returning a
    status dict, so the MCP framework represents the failure as a proper
    tool-call error (``CallToolResult(is_error=True)``) to the client,
    rather than a "successful" call whose payload happens to describe a
    failure.
    """
    if result["status"] != "success":
        errors = "; ".join(result.get("errors") or []) or "no error detail"
        raise ToolError(f"{result['status']}: {errors}")
    return result["data"]  # type: ignore[no-any-return]


@mcp_server.tool()
def evaluate_riasec_profile(
    realistic: int,
    investigative: int,
    artistic: int,
    social: int,
    enterprising: int,
    conventional: int,
) -> dict[str, Any]:
    """Score a RIASEC vocational profile from six 1-10 dimension ratings.

    Args:
        realistic: 1-10 rating for hands-on/mechanical interest.
        investigative: 1-10 rating for analytical/scientific interest.
        artistic: 1-10 rating for creative/expressive interest.
        social: 1-10 rating for helping/teaching interest.
        enterprising: 1-10 rating for leading/persuading interest.
        conventional: 1-10 rating for organizing/detail-oriented interest.
    """
    return _unwrap_or_raise(
        evaluate_riasec_profile_handler(
            realistic=realistic,
            investigative=investigative,
            artistic=artistic,
            social=social,
            enterprising=enterprising,
            conventional=conventional,
        )
    )


@mcp_server.tool()
def search_careers(query: str, field: str | None = None) -> dict[str, Any]:
    """Search the career catalog by free-text query and optional field filter.

    Args:
        query: Free-text search query (career name, keyword, etc.).
        field: Optional field filter (e.g. "Tecnología", "Salud").
    """
    return _unwrap_or_raise(search_careers_handler(query=query, field=field))


@mcp_server.tool()
def calculate_affinity(riasec_code: str, top_n: int = 5) -> dict[str, Any]:
    """Rank careers by affinity to a 3-letter RIASEC code.

    Args:
        riasec_code: 3-letter RIASEC code (e.g. "IAS").
        top_n: Maximum number of ranked careers to return (default 5).
    """
    return _unwrap_or_raise(calculate_affinity_handler(riasec_code=riasec_code, top_n=top_n))


@mcp_server.tool()
async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web for current career/education information.

    Args:
        query: Search query (be specific for better results).
        max_results: Maximum number of results to return (default 5).
    """
    return _unwrap_or_raise(await web_search_handler(query=query, max_results=max_results))


def main() -> None:
    """Entry point for ``python -m src.mcp`` -- runs the stdio MCP server."""
    mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
