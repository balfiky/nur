"""Agentic tools package — execution surfaces for Nūr cognition."""

from __future__ import annotations

from tools.registry import ToolRegistry
from tools.executor import ToolExecutor
from tools.builtin import filesystem, shell, web_search
from tools.builtin.web_search import WebProvider


def register_builtins(
    registry: ToolRegistry,
    executor: ToolExecutor,
    web_provider: WebProvider | None = None,
) -> None:
    """Register all builtin tool capabilities and handlers.

    Args:
        registry: The tool registry to register capabilities into.
        executor: The executor to bind handlers to.
        web_provider: Optional web provider; defaults to NullWebProvider.
    """
    # Filesystem
    for cap in filesystem.CAPABILITIES:
        registry.register(cap)
    for name, handler in filesystem.HANDLERS.items():
        executor.register_handler(name, handler)

    # Shell
    for cap in shell.CAPABILITIES:
        registry.register(cap)
    for name, handler in shell.HANDLERS.items():
        executor.register_handler(name, handler)

    # Web search/fetch
    for cap in web_search.CAPABILITIES:
        registry.register(cap)
    web_handlers = web_search.create_handlers(web_provider)
    for name, handler in web_handlers.items():
        executor.register_handler(name, handler)
