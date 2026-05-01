"""Tests for OpenAI-compatible native tool-call orchestration."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from core.emotional_engine import EmotionalEngine
from core.life_influence import LifeInfluence
from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools import register_builtins
from nur_tools.executor import ToolExecutor
from nur_tools.native_orchestrator import NativeToolCallRunner
from nur_tools.registry import ToolRegistry


class FakeNativeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected native model call")
        return self.responses.pop(0)


def _response(*tool_calls: Any, content: str | None = None) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    role="assistant",
                    content=content,
                    tool_calls=list(tool_calls) or None,
                )
            )
        ]
    )


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


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


def test_native_runner_requires_structured_tool_choice() -> None:
    client = FakeNativeClient([
        _response(_tool_call("system__disk_usage", {"path": "/"})),
    ])
    runner = NativeToolCallRunner(
        executor=_executor(),
        base_url="http://localhost:8002/v1",
        model="test-model",
        client=client,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message="how much disk space is left?",
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
        max_executions=1,
        autonomy_level="high_risk",
    )

    assert client.requests[0]["model"] == "test-model"
    assert client.requests[0]["tool_choice"] == "required"
    tool_names = {
        item["function"]["name"] for item in client.requests[0]["tools"]
    }
    assert "system__disk_usage" in tool_names
    assert "control__no_tool" in tool_names
    assert result.trace.loop_count == 1
    assert result.trace.executed_results[0].tool_name == "system.disk_usage"
    assert "output='Path Size Used Avail Use%" in result.tool_context_summary


def test_native_runner_executes_shell_tool_call_without_regex() -> None:
    client = FakeNativeClient([
        _response(_tool_call("shell__run_command", {"cmd": "df -h /"})),
    ])
    runner = NativeToolCallRunner(
        executor=_fake_shell_executor(),
        base_url="http://localhost:8002/v1",
        model="test-model",
        client=client,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message="great...give me your remaining disk space",
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
        max_executions=1,
        autonomy_level="high_risk",
    )

    assert result.trace.loop_count == 1
    assert result.trace.proposed_intents[0].reason == (
        "OpenAI-compatible model-native tool call"
    )
    assert result.trace.executed_results[0].metadata["cmd"] == "df -h /"
    assert "ran df -h /" in result.tool_context_summary


def test_native_runner_exposes_installed_packages_tool() -> None:
    client = FakeNativeClient([
        _response(_tool_call("system__installed_packages", {"prefix": "nvidia"})),
    ])
    runner = NativeToolCallRunner(
        executor=_executor(),
        base_url="http://localhost:8002/v1",
        model="test-model",
        client=client,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message='list apt packages starting with "nvidia"',
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
        max_executions=1,
        autonomy_level="high_risk",
    )

    tool_names = {
        item["function"]["name"] for item in client.requests[0]["tools"]
    }
    assert "system__installed_packages" in tool_names
    assert result.trace.loop_count == 1
    assert result.trace.executed_results[0].tool_name == "system.installed_packages"
    assert result.trace.executed_results[0].metadata["prefix"] == "nvidia"


def test_native_runner_returns_empty_trace_when_model_chooses_no_tool() -> None:
    client = FakeNativeClient([
        _response(_tool_call("control__no_tool", {"reason": "greeting only"})),
    ])
    runner = NativeToolCallRunner(
        executor=_executor(),
        base_url="http://localhost:8002/v1",
        model="test-model",
        client=client,
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


def test_native_runner_accepts_life_influence_and_records_action_effect() -> None:
    client = FakeNativeClient([
        _response(_tool_call("system__hostname", {})),
    ])
    runner = NativeToolCallRunner(
        executor=_executor(),
        base_url="http://localhost:8002/v1",
        model="test-model",
        client=client,
    )
    engine = EmotionalEngine()

    result = runner.run_tool_loop(
        user_message="what is your hostname?",
        state=engine.state,
        person=None,
        defense_active=False,
        engine=engine,
        max_executions=1,
        autonomy_level="high_risk",
        life_influence=LifeInfluence(competence_pressure=0.04),
    )

    assert result.trace.loop_count == 1
    assert result.life_influence_effects["persistence_delta"] == 0.04
    assert result.action_variables.persistence_drive > 0.5
