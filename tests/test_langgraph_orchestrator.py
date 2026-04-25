"""Tests for LangGraph-backed tool orchestration."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from core.emotional_engine import EmotionalEngine
from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools import register_builtins
from nur_tools.executor import ToolExecutor
from nur_tools.langgraph_orchestrator import LangGraphToolRunner
from nur_tools.registry import ToolRegistry


def _executor() -> ToolExecutor:
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    register_builtins(registry, executor)
    return executor


def _fake_shell_executor() -> ToolExecutor:
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    registry.register(
        ToolCapability(
            name="shell.run_command",
            description="Run a shell command and capture output",
            category=ToolCategory.DESTRUCTIVE,
            arg_schema={"cmd": {"type": "string", "required": True}},
        )
    )

    def run_command(args: dict[str, object]) -> ToolResult:
        cmd = str(args["cmd"])
        return ToolResult(
            tool_name="shell.run_command",
            success=True,
            output=f"ran {cmd}",
            metadata={"cmd": cmd},
            side_effect_summary="command executed",
        )

    executor.register_handler("shell.run_command", run_command)
    return executor


def test_langgraph_shell_call_returns_tool_context() -> None:
    model = FakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "shell__run_command",
                        "args": {"cmd": "printf langgraph-visible"},
                        "id": "call_1",
                    },
                ],
            ),
            AIMessage(content="done"),
        ],
    )
    executor = _executor()
    runner = LangGraphToolRunner(
        executor=executor,
        base_url="http://localhost:8000/v1",
        model="test",
        chat_model=model,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message="check the disk space",
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
        autonomy_level="high_risk",
    )

    assert result.trace.loop_count == 1
    assert result.trace.proposed_intents[0].tool_name == "shell.run_command"
    assert result.trace.executed_results[0].tool_name == "shell.run_command"
    assert "output='langgraph-visible'" in result.tool_context_summary


def test_langgraph_no_tool_call_returns_empty_trace_without_fallback() -> None:
    model = FakeMessagesListChatModel(responses=[AIMessage(content="no tool needed")])
    runner = LangGraphToolRunner(
        executor=_executor(),
        base_url="http://localhost:8000/v1",
        model="test",
        chat_model=model,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message="hello",
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
    )

    assert result.trace.loop_count == 0
    assert result.tool_context_summary == ""


def test_hybrid_falls_back_to_heuristic_when_model_skips_tool() -> None:
    model = FakeMessagesListChatModel(responses=[AIMessage(content="df -h /")])
    runner = LangGraphToolRunner(
        executor=_executor(),
        base_url="http://localhost:8000/v1",
        model="test",
        fallback_to_heuristic=True,
        chat_model=model,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message="how much disk space left",
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
        autonomy_level="high_risk",
    )

    assert result.trace.loop_count == 1
    assert result.trace.executed_results[0].metadata["cmd"] == "df -h /"
    assert "Filesystem" in result.tool_context_summary


def test_hybrid_falls_back_for_explicit_unquoted_shell_command() -> None:
    model = FakeMessagesListChatModel(responses=[AIMessage(content="Done.")])
    runner = LangGraphToolRunner(
        executor=_fake_shell_executor(),
        base_url="http://localhost:8000/v1",
        model="test",
        fallback_to_heuristic=True,
        chat_model=model,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message="do arbitrary-tool --flag",
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
        autonomy_level="high_risk",
    )

    assert result.trace.loop_count == 1
    assert result.trace.executed_results[0].metadata["cmd"] == "arbitrary-tool --flag"
    assert "ran arbitrary-tool --flag" in result.tool_context_summary
