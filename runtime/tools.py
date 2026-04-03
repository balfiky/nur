"""Runtime tool wiring for production surfaces."""

from __future__ import annotations

from tools import register_builtins
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def create_tool_executor() -> ToolExecutor:
    """Create a per-pipeline tool executor with builtin tools registered."""
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    register_builtins(registry, executor)
    return executor
