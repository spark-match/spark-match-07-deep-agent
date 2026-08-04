"""MCP server package (Sprint 8, task 8.5).

See :mod:`src.mcp.server` for the actual server implementation and scope
decision (exposes this project's 4 tools; does not consume external MCP
servers).
"""

from src.mcp.server import mcp_server

__all__ = ["mcp_server"]
