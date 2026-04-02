"""Tool executor — thin orchestrator for tool execution.

Looks up a tool handler by name, calls it, normalizes all outcomes
(including exceptions) into structured ToolResult values.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from core.types import ToolResult
from tools.registry import ToolRegistry

# Handler signature: takes arguments dict, returns ToolResult
ToolHandler = Callable[[dict[str, Any]], ToolResult]


class ToolExecutor:
    """Executes tools by name using registered handlers.

    The executor is intentionally thin: lookup, call, normalize.
    Cognitive decisions about *whether* to execute live elsewhere.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._handlers: dict[str, ToolHandler] = {}

    def register_handler(self, tool_name: str, handler: ToolHandler) -> None:
        """Bind an executable handler to a registered tool name."""
        self._handlers[tool_name] = handler

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with the given arguments.

        Returns a structured ToolResult in all cases — never raises.
        """
        start = time.perf_counter()

        # Check tool exists in registry
        capability = self._registry.get(tool_name)
        if capability is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Unknown tool: {tool_name}",
                latency_ms=_elapsed_ms(start),
            )

        # Check handler exists
        handler = self._handlers.get(tool_name)
        if handler is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"No handler registered for tool: {tool_name}",
                latency_ms=_elapsed_ms(start),
            )

        # Execute and normalize
        try:
            result = handler(arguments)
            result.latency_ms = _elapsed_ms(start)
            return result
        except Exception as exc:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=_elapsed_ms(start),
            )


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000
