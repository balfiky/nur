"""MCP client abstraction — protocol-based, easy to mock.

Defines the MCPClient protocol that any MCP transport can implement.
Ships with NullMCPClient (errors on call) for safe defaults.

When a real MCP SDK is available, a concrete implementation can be
plugged in without changing any Nūr code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# MCP tool metadata (what discovery returns)
# ---------------------------------------------------------------------------

@dataclass
class MCPToolInfo:
    """Metadata for a single MCP-discovered tool.

    This is the normalized representation of what an MCP server
    advertises. The adapter converts this into ToolCapability.
    """
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""  # which MCP server provides this


# ---------------------------------------------------------------------------
# MCP call result (what execution returns)
# ---------------------------------------------------------------------------

@dataclass
class MCPCallResult:
    """Raw result from calling an MCP tool.

    The adapter converts this into ToolResult.
    """
    content: str = ""
    is_error: bool = False
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MCPClient protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class MCPClient(Protocol):
    """Protocol for MCP server communication.

    Implementations handle transport (stdio, SSE, HTTP) and session
    lifecycle. The protocol is intentionally minimal — discover + call.
    """

    def discover_tools(self) -> list[MCPToolInfo]:
        """List all tools available from the MCP server."""
        ...

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPCallResult:
        """Call a named MCP tool with arguments.

        Must not raise — all errors should be returned as MCPCallResult
        with is_error=True.
        """
        ...


# ---------------------------------------------------------------------------
# NullMCPClient — safe default when no MCP server is configured
# ---------------------------------------------------------------------------

class NullMCPClient:
    """MCP client that has no server — errors on any call.

    Used as default when MCP is not configured, similar to how
    NullWebProvider works for web search.
    """

    def discover_tools(self) -> list[MCPToolInfo]:
        return []

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPCallResult:
        return MCPCallResult(
            is_error=True,
            error_message="No MCP server configured",
        )
