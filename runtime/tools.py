"""Runtime tool wiring for production surfaces.

External agentic tools are opt-in via ``RuntimeConfig.tools_enabled``. When
disabled (the default), :func:`create_tool_executor` still returns a restricted
executor containing only Nūr's internal skill-registry tools, so the assistant
can create/audit/enable durable skills through the observable tool trace.
Filesystem, browser, web, calendar, and shell capabilities remain excluded
until tools are enabled; ``shell.run_command`` also requires the separate
``shell_tool_enabled`` flag.
"""

from __future__ import annotations

from typing import Optional

from runtime.config import RuntimeConfig
from nur_tools import register_builtins, register_skill_builtins
from nur_tools.executor import ToolExecutor
from nur_tools.registry import ToolRegistry
from nur_tools.builtin.web_provider import RequestsWebProvider
from nur_tools.native_orchestrator import NativeToolCallRunner


def create_tool_executor(
    config: RuntimeConfig | None = None,
) -> Optional[ToolExecutor]:
    """Create a per-pipeline tool executor.

    Preserves the zero-arg signature for back-compat with tests that wire the
    factory directly; those tests receive a fully-enabled, unsandboxed
    executor to keep the phase-0..8 agentic-tool suites green. Production
    callers pass a ``RuntimeConfig`` so external tools honor the
    ``tools_enabled`` / ``shell_tool_enabled`` / ``tools_workspace`` gates.
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

    registry = ToolRegistry()
    executor = ToolExecutor(registry)

    if not config.tools_enabled:
        register_skill_builtins(registry, executor, skill_config=config)
        return executor

    web_provider = RequestsWebProvider()
    register_builtins(
        registry,
        executor,
        web_provider=web_provider,
        fs_workspace=config.resolved_tools_workspace,
        include_shell=config.shell_tool_enabled,
        skill_config=config,
    )
    executor._owned_resources = [web_provider]
    if config.llm_base_url.strip() and config.llm_model.strip():
        effective_key = config.llm_api_key or config.minimax_api_key
        executor._tool_runner = NativeToolCallRunner(
            executor=executor,
            base_url=config.llm_base_url,
            model=config.llm_model,
            api_key=effective_key,
        )
        executor._owned_resources.append(executor._tool_runner)
    return executor
