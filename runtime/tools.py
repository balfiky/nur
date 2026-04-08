"""Runtime tool wiring for production surfaces."""

from __future__ import annotations

from tools import register_builtins
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from tools.builtin.web_provider import RequestsWebProvider


def create_tool_executor() -> ToolExecutor:
    """Create a per-pipeline tool executor with builtin tools registered."""
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    web_provider = RequestsWebProvider()
    register_builtins(registry, executor, web_provider=web_provider)
    # Record closable resources so pipeline.close() can release HTTP pools.
    executor._owned_resources = [web_provider]
    return executor
