"""MCP adapter — normalizes MCP tools into Nūr's internal model.

Responsibilities:
  1. Convert MCPToolInfo → ToolCapability (with category assignment)
  2. Create handler functions that call MCPClient and return ToolResult
  3. Register discovered tools into the shared ToolRegistry + ToolExecutor

MCP tools appear as normal participants in the cognitive tool loop:
same arbiter, same appraisal, same memory coupling.
"""

from __future__ import annotations

from typing import Any

from core.types import ToolCapability, ToolCategory, ToolResult
from tools.mcp.client import MCPClient, MCPToolInfo
from tools.registry import ToolRegistry
from tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# Category assignment — explicit and deterministic
# ---------------------------------------------------------------------------

# Known patterns for category inference from tool name/description.
# MCP servers don't declare categories, so we infer from naming conventions.
_CATEGORY_KEYWORDS: dict[ToolCategory, list[str]] = {
    ToolCategory.READ_ONLY: [
        "get", "list", "read", "search", "fetch", "query", "find",
        "show", "describe", "info", "status", "view",
    ],
    ToolCategory.WRITE: [
        "create", "write", "update", "set", "add", "put", "edit",
        "modify", "patch", "insert", "upload",
    ],
    ToolCategory.DESTRUCTIVE: [
        "delete", "remove", "drop", "destroy", "purge", "clear",
        "revoke", "uninstall", "terminate", "kill",
    ],
    ToolCategory.EXTERNAL_ACTION: [
        "send", "post", "publish", "notify", "email", "message",
        "invoke", "trigger", "execute", "run", "call",
    ],
}

# Explicit overrides for well-known MCP tool patterns
_CATEGORY_OVERRIDES: dict[str, ToolCategory] = {}


def infer_category(tool_name: str, description: str = "") -> ToolCategory:
    """Infer a ToolCategory from an MCP tool's name and description.

    Strategy:
      1. Check explicit overrides
      2. Match keywords against tool name (strongest signal)
      3. Match keywords against description (weaker signal)
      4. Default to EXTERNAL_ACTION (safest for unknown MCP tools)
    """
    # Explicit override
    if tool_name in _CATEGORY_OVERRIDES:
        return _CATEGORY_OVERRIDES[tool_name]

    name_lower = tool_name.lower().replace("-", "_").replace(".", "_")
    desc_lower = description.lower()

    # Check name first (strongest signal)
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return category

    # Fall back to description
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in desc_lower:
                return category

    # Unknown MCP tool — default to EXTERNAL_ACTION (treated cautiously by arbiter)
    return ToolCategory.EXTERNAL_ACTION


# ---------------------------------------------------------------------------
# MCPAdapter — discovery + handler creation
# ---------------------------------------------------------------------------

class MCPAdapter:
    """Adapts MCP tools into Nūr's internal tool model.

    Usage:
        client = SomeMCPClient(...)
        adapter = MCPAdapter(client, server_name="calendar")
        capabilities, handlers = adapter.discover_and_adapt()
        # Then register into ToolRegistry + ToolExecutor
    """

    def __init__(
        self,
        client: MCPClient,
        server_name: str = "mcp",
        namespace: str | None = None,
    ) -> None:
        """Create an adapter.

        Args:
            client: MCP client implementation.
            server_name: Name of the MCP server (for logging/debug).
            namespace: Tool name prefix. Defaults to "mcp.{server_name}".
        """
        self._client = client
        self._server_name = server_name
        self._namespace = namespace or f"mcp.{server_name}"

    def discover_and_adapt(self) -> tuple[list[ToolCapability], dict[str, Any]]:
        """Discover MCP tools and convert to Nūr capabilities + handlers.

        Returns:
            (capabilities, handlers) where handlers maps tool_name → callable.
        """
        mcp_tools = self._client.discover_tools()
        capabilities: list[ToolCapability] = []
        handlers: dict[str, Any] = {}

        for info in mcp_tools:
            nür_name = f"{self._namespace}.{info.name}"
            cap = self._to_capability(info, nür_name)
            handler = self._create_handler(info.name, nür_name)
            capabilities.append(cap)
            handlers[nür_name] = handler

        return capabilities, handlers

    def _to_capability(self, info: MCPToolInfo, nür_name: str) -> ToolCapability:
        """Convert MCPToolInfo to ToolCapability."""
        category = infer_category(info.name, info.description)
        return ToolCapability(
            name=nür_name,
            description=info.description or f"MCP tool: {info.name}",
            category=category,
            arg_schema=info.input_schema,
            requires_network=True,  # MCP tools typically need network
            mcp_backed=True,
        )

    def _create_handler(
        self, mcp_name: str, nür_name: str,
    ) -> Any:
        """Create a handler function that calls the MCP tool.

        The handler captures the client and MCP tool name via closure.
        Returns ToolResult — never raises.
        """
        client = self._client

        def handler(args: dict[str, Any]) -> ToolResult:
            try:
                result = client.call_tool(mcp_name, args)
                if result.is_error:
                    return ToolResult(
                        tool_name=nür_name,
                        success=False,
                        output="",
                        error=result.error_message or "MCP tool error",
                        metadata=result.metadata,
                    )
                return ToolResult(
                    tool_name=nür_name,
                    success=True,
                    output=result.content,
                    metadata=result.metadata,
                )
            except Exception as exc:
                return ToolResult(
                    tool_name=nür_name,
                    success=False,
                    output="",
                    error=f"MCP call failed: {type(exc).__name__}: {exc}",
                )

        return handler


# ---------------------------------------------------------------------------
# Convenience: register MCP tools into existing registry + executor
# ---------------------------------------------------------------------------

def register_mcp_tools(
    client: MCPClient,
    registry: ToolRegistry,
    executor: ToolExecutor,
    server_name: str = "mcp",
    namespace: str | None = None,
) -> list[str]:
    """Discover and register MCP tools into the shared registry + executor.

    This is the primary entry point for MCP integration. Call it once
    per MCP server at startup.

    Args:
        client: MCP client implementation.
        registry: Shared tool registry (already has builtins).
        executor: Shared tool executor.
        server_name: Name for this MCP server.
        namespace: Optional override for tool name prefix.

    Returns:
        List of registered tool names (namespaced).
    """
    adapter = MCPAdapter(client, server_name=server_name, namespace=namespace)
    capabilities, handlers = adapter.discover_and_adapt()

    registered: list[str] = []
    for cap in capabilities:
        registry.register(cap)
        registered.append(cap.name)

    for name, handler in handlers.items():
        executor.register_handler(name, handler)

    return registered
