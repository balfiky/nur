"""MCP bridge — adapts Model Context Protocol tools into Nūr's internal model.

MCP tools are treated identically to builtin tools once registered:
same ToolCapability, same ToolResult, same cognitive appraisal path.
"""

from nur_tools.mcp.client import MCPClient, NullMCPClient, MCPToolInfo
from nur_tools.mcp.adapter import MCPAdapter, register_mcp_tools

__all__ = [
    "MCPClient",
    "NullMCPClient",
    "MCPToolInfo",
    "MCPAdapter",
    "register_mcp_tools",
]
