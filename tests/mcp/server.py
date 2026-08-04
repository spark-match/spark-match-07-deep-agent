"""Tests for the MCP server (Sprint 8, task 8.5).

Exercises the real MCPServer instance end-to-end via its own
list_tools()/call_tool() API — the same surface a real MCP client
(stdio/SSE transport) would drive — rather than calling the registered
Python functions directly, so these tests also catch registration
mistakes (wrong name, dropped tool, broken signature introspection).
"""

import pytest

from src import budget
from src.mcp import mcp_server
from src.mcp.server import evaluate_riasec_profile
from src.tools.web_search import handler as web_search_module


@pytest.fixture(autouse=True)
def _clean_state():
    """Fresh settings cache and budget counter for every test (web_search)."""
    from src.config import get_settings

    get_settings.cache_clear()
    budget.reset_session_budget()
    yield
    get_settings.cache_clear()
    budget.reset_session_budget()


class TestServerIdentity:
    def test_server_name(self):
        assert mcp_server.name == "spark-match-agent"


class TestToolRegistration:
    async def test_all_four_tools_are_registered(self):
        tools = await mcp_server.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "evaluate_riasec_profile",
            "search_careers",
            "calculate_affinity",
            "web_search",
        }


class TestEvaluateRiasecProfileTool:
    async def test_success_returns_structured_content(self):
        result = await mcp_server.call_tool(
            "evaluate_riasec_profile",
            {
                "realistic": 2,
                "investigative": 8,
                "artistic": 7,
                "social": 4,
                "enterprising": 3,
                "conventional": 5,
            },
        )
        assert result.is_error is False
        assert "riasec_code" in result.structured_content
        assert "dominant_types" in result.structured_content

    async def test_invalid_scores_raise_tool_error(self):
        """The handler returns status="error" for out-of-range scores;
        the MCP wrapper must raise ToolError rather than surface a
        "successful" call whose payload is actually a failure."""
        from mcp.server.mcpserver.exceptions import ToolError

        with pytest.raises(ToolError):
            await mcp_server.call_tool(
                "evaluate_riasec_profile",
                {
                    "realistic": 0,
                    "investigative": 0,
                    "artistic": 0,
                    "social": 0,
                    "enterprising": 0,
                    "conventional": 0,
                },
            )

    def test_registered_function_delegates_to_the_same_handler_the_langchain_tool_uses(self):
        """Both protocol adapters (LangChain @tool and this MCP tool) must
        share one implementation -- not two copies that can drift apart."""
        from src.tools.assessment.handler import evaluate_riasec_profile_handler

        result = evaluate_riasec_profile(
            realistic=2,
            investigative=8,
            artistic=7,
            social=4,
            enterprising=3,
            conventional=5,
        )
        expected = evaluate_riasec_profile_handler(
            realistic=2,
            investigative=8,
            artistic=7,
            social=4,
            enterprising=3,
            conventional=5,
        )["data"]
        assert result == expected


class TestSearchCareersTool:
    async def test_success_returns_structured_content(self):
        result = await mcp_server.call_tool("search_careers", {"query": "psicolog"})
        assert result.is_error is False
        assert "careers" in result.structured_content


class TestCalculateAffinityTool:
    async def test_success_returns_structured_content(self):
        result = await mcp_server.call_tool(
            "calculate_affinity", {"riasec_code": "IRC", "top_n": 3}
        )
        assert result.is_error is False
        assert "matches" in result.structured_content
        assert len(result.structured_content["matches"]) <= 3


class TestWebSearchTool:
    """Monkeypatches the same module-level provider functions
    tests/tools/web_search.py patches -- web_search_handler (imported by
    src/mcp/server.py) calls _search_tavily/_search_duckduckgo regardless
    of which caller (LangChain tool or this MCP tool) invoked it."""

    async def test_success_returns_structured_content(self, monkeypatch):
        async def fake_tavily(*_a):
            return [{"title": "r1", "url": "https://example.com/1", "content": "x"}]

        monkeypatch.setattr(web_search_module, "_search_tavily", fake_tavily)

        result = await mcp_server.call_tool("web_search", {"query": "python careers"})

        assert result.is_error is False
        assert result.structured_content["provider"] == "tavily"

    async def test_both_providers_failing_raises_tool_error(self, monkeypatch):
        from mcp.server.mcpserver.exceptions import ToolError

        async def raising_tavily(*_a):
            raise RuntimeError("tavily down")

        def raising_ddg(*_a):
            raise RuntimeError("ddg down")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", raising_ddg)

        with pytest.raises(ToolError):
            await mcp_server.call_tool("web_search", {"query": "python careers"})
