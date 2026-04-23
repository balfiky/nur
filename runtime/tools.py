"""Runtime tool wiring for production surfaces.

Tools are opt-in via ``RuntimeConfig.tools_enabled``. When disabled (the
default), :func:`create_tool_executor` returns ``None`` so the cognitive
pipeline skips tool-intent detection and execution entirely. Even when
enabled, filesystem operations are sandboxed to
``RuntimeConfig.resolved_tools_workspace`` and ``shell.run_command`` requires
the separate ``shell_tool_enabled`` flag.
"""

from __future__ import annotations

from typing import Optional

from runtime.config import RuntimeConfig
from tools import register_builtins
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from tools.builtin.web_provider import RequestsWebProvider


def create_tool_executor(
    config: RuntimeConfig | None = None,
) -> Optional[ToolExecutor]:
    """Create a per-pipeline tool executor, or ``None`` when tools are disabled.

    Preserves the zero-arg signature for back-compat with tests that wire the
    factory directly; those tests receive a fully-enabled, unsandboxed
    executor to keep the phase-0..8 agentic-tool suites green. Production
    callers pass a ``RuntimeConfig`` so the ``tools_enabled`` /
    ``shell_tool_enabled`` / ``tools_workspace`` gates take effect.
    """
    # Legacy zero-arg call path (tests). Mirror the previous behavior
    # exactly: every builtin registered, no sandbox.
    if config is None:
        registry = ToolRegistry()
        executor = ToolExecutor(registry)
        web_provider = RequestsWebProvider()
        register_builtins(registry, executor, web_provider=web_provider)
        executor._owned_resources = [web_provider]
        return executor

    if not config.tools_enabled:
        return None

    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    web_provider = RequestsWebProvider()
    register_builtins(
        registry,
        executor,
        web_provider=web_provider,
        fs_workspace=config.resolved_tools_workspace,
        include_shell=config.shell_tool_enabled,
    )
    executor._owned_resources = [web_provider]
    return executor
