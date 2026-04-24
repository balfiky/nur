"""Tool registry — registration and discovery of available tools.

No execution logic. Tools register their ToolCapability here;
the cognitive layer queries the registry to know what's available.
"""

from __future__ import annotations

from core.types import ToolCapability, ToolCategory


class ToolRegistry:
    """Central registry of available tool capabilities."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolCapability] = {}

    def register(self, tool: ToolCapability) -> None:
        """Register a tool capability. Overwrites if name already exists."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolCapability | None:
        """Look up a tool by name. Returns None if not found."""
        return self._tools.get(name)

    def list_tools(self, category: ToolCategory | None = None) -> list[ToolCapability]:
        """List all registered tools, optionally filtered by category."""
        tools = list(self._tools.values())
        if category is not None:
            tools = [t for t in tools if t.category == category]
        return tools

    def names(self) -> list[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
