"""Agentic tools package — execution surfaces for Nūr cognition."""

from __future__ import annotations

from tools.registry import ToolRegistry
from tools.executor import ToolExecutor
from tools.builtin import filesystem, shell, web_search
from tools.builtin import browser as browser_mod
from tools.builtin import calendar as calendar_mod
from tools.builtin.web_search import WebProvider
from tools.builtin.browser import BrowserProvider
from tools.builtin.calendar import CalendarProvider
from tools.mcp.adapter import register_mcp_tools
from tools.mcp.client import MCPClient


def register_builtins(
    registry: ToolRegistry,
    executor: ToolExecutor,
    web_provider: WebProvider | None = None,
    browser_provider: BrowserProvider | None = None,
    calendar_provider: CalendarProvider | None = None,
    *,
    fs_workspace: str | None = None,
    include_shell: bool = True,
) -> None:
    """Register all builtin tool capabilities and handlers.

    Args:
        registry: The tool registry to register capabilities into.
        executor: The executor to bind handlers to.
        web_provider: Optional web provider; defaults to NullWebProvider.
        browser_provider: Optional browser provider; defaults to NullBrowserProvider.
        calendar_provider: Optional calendar provider; defaults to NullCalendarProvider.
        fs_workspace: Optional workspace root; when set, filesystem tools
            refuse paths that resolve outside this directory.
        include_shell: Whether to register ``shell.run_command``. Defaults to
            True to preserve existing test wiring; production callers gate
            this on an explicit config flag.
    """
    # Filesystem
    for cap in filesystem.CAPABILITIES:
        registry.register(cap)
    fs_handlers = filesystem.create_handlers(workspace=fs_workspace)
    for name, handler in fs_handlers.items():
        executor.register_handler(name, handler)

    # Shell
    if include_shell:
        for cap in shell.CAPABILITIES:
            registry.register(cap)
        for name, handler in shell.HANDLERS.items():
            executor.register_handler(name, handler)

    # Web search/fetch/extract
    for cap in web_search.CAPABILITIES:
        registry.register(cap)
    web_handlers = web_search.create_handlers(web_provider)
    for name, handler in web_handlers.items():
        executor.register_handler(name, handler)

    # Browser
    for cap in browser_mod.CAPABILITIES:
        registry.register(cap)
    browser_handlers = browser_mod.create_handlers(browser_provider)
    for name, handler in browser_handlers.items():
        executor.register_handler(name, handler)

    # Calendar
    for cap in calendar_mod.CAPABILITIES:
        registry.register(cap)
    calendar_handlers = calendar_mod.create_handlers(calendar_provider)
    for name, handler in calendar_handlers.items():
        executor.register_handler(name, handler)
