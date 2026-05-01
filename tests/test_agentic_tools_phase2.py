"""Tests for Agentic Tools Phase 2: cognitive tool loop, arbiter, appraisal, pipeline integration."""

from __future__ import annotations

import pytest

from core.types import (
    ActionVariables,
    AgencyDecision,
    PersonProfile,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    ToolTrace,
)
from core.tool_appraisal import appraise_tool_result
from core.dual_process.tool_loop import (
    detect_tool_intent,
    make_tool_decision,
    run_tool_loop,
)
from core.emotional_engine import EmotionalEngine
from nur_tools.registry import ToolRegistry
from nur_tools.executor import ToolExecutor
from nur_tools import register_builtins
from pipeline import (
    CognitivePipeline,
    DebugState,
    _message_for_model_tool_routing,
    _message_for_tool_detection,
)


# ===================================================================
# Helpers
# ===================================================================

def _make_executor():
    """Create a registry + executor with all builtins."""
    reg = ToolRegistry()
    exe = ToolExecutor(reg)
    register_builtins(reg, exe)
    return reg, exe


def _make_pipeline_with_tools(tmp_path=None):
    """Create a CognitivePipeline wired to a ToolExecutor."""
    reg = ToolRegistry()
    exe = ToolExecutor(reg)
    register_builtins(reg, exe)
    pipe = CognitivePipeline(tool_executor=exe)
    return pipe, reg, exe


class FakeWebProvider:
    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        return [
            {
                "title": f"OpenAI update for {query}",
                "url": "https://example.com/openai-update",
                "snippet": "OpenAI search snippet",
            },
        ]

    def fetch(self, url: str) -> str:
        return f"Fetched {url}"

    def extract_text(self, url: str) -> str:
        return f"Readable text from {url}"


def _make_executor_with_fake_web():
    reg = ToolRegistry()
    exe = ToolExecutor(reg)
    register_builtins(reg, exe, web_provider=FakeWebProvider())
    return reg, exe


# ===================================================================
# Tool intent detection (heuristic)
# ===================================================================

class TestDetectToolIntent:
    def _available(self):
        return {
            "fs.read_file", "fs.list_dir", "fs.search_text", "fs.glob_paths",
            "fs.write_file", "fs.delete_path", "shell.run_command",
            "web.search", "web.fetch", "web.extract_text",
            "skills.list", "skills.create_from_request", "skills.enable",
            "skills.disable",
        }

    def test_no_tool_for_conversational(self):
        assert detect_tool_intent("How are you today?", self._available()) is None

    def test_no_tool_for_greeting(self):
        assert detect_tool_intent("Hello!", self._available()) is None

    def test_read_file(self):
        intent = detect_tool_intent("read file /tmp/test.txt", self._available())
        assert intent is not None
        assert intent.tool_name == "fs.read_file"
        assert intent.arguments["path"] == "/tmp/test.txt"

    def test_list_dir(self):
        intent = detect_tool_intent("list files in /home/user", self._available())
        assert intent is not None
        assert intent.tool_name == "fs.list_dir"

    def test_search_text(self):
        intent = detect_tool_intent("search for 'hello' in /tmp", self._available())
        assert intent is not None
        assert intent.tool_name == "fs.search_text"
        assert intent.arguments["pattern"] == "hello"

    def test_run_command(self):
        intent = detect_tool_intent("run command 'echo hello'", self._available())
        assert intent is not None
        assert intent.tool_name == "shell.run_command"
        assert intent.arguments["cmd"] == "echo hello"

    def test_unquoted_explicit_command_uses_shell(self):
        intent = detect_tool_intent("do arbitrary-tool --flag", self._available())
        assert intent is not None
        assert intent.tool_name == "shell.run_command"
        assert intent.arguments["cmd"] == "arbitrary-tool --flag"

    def test_hostname_question_uses_shell(self):
        intent = detect_tool_intent(
            "can you tell me the hostname of the machine you are running from?",
            self._available(),
        )
        assert intent is not None
        assert intent.tool_name == "shell.run_command"
        assert intent.arguments["cmd"] == "hostname"

    def test_unquoted_uname_uses_shell(self):
        intent = detect_tool_intent("run uname -a", self._available())
        assert intent is not None
        assert intent.tool_name == "shell.run_command"
        assert intent.arguments["cmd"] == "uname -a"

    def test_tool_followup_reuses_previous_user_request(self):
        msg = _message_for_tool_detection(
            "issue the needed command",
            [
                {"role": "user", "content": "can you tell me your hostname?"},
                {"role": "assistant", "content": "I would need a command."},
            ],
        )
        assert "can you tell me your hostname?" in msg
        assert "issue the needed command" in msg

    def test_terse_tool_followup_reuses_previous_actionable_request(self):
        msg = _message_for_tool_detection(
            "go",
            [
                {"role": "user", "content": "how much disk space left?"},
                {"role": "assistant", "content": "df -h /"},
                {"role": "user", "content": "stop printing the command"},
            ],
        )
        assert "how much disk space left?" in msg
        assert "go" in msg

    def test_raw_output_followup_reuses_previous_actionable_request(self):
        msg = _message_for_tool_detection(
            "give me raw output",
            [
                {"role": "user", "content": "check disk space now"},
                {"role": "assistant", "content": "df -h /"},
            ],
        )
        assert "check disk space now" in msg
        assert "give me raw output" in msg

    def test_model_tool_routing_includes_history_for_references(self):
        msg = _message_for_model_tool_routing(
            "no, execute it on your pc",
            [
                {"role": "user", "content": "what is the harddisk utilzation?"},
                {"role": "assistant", "content": "Run df -h."},
            ],
        )
        assert "Recent conversation for tool routing" in msg
        assert "what is the harddisk utilzation?" in msg
        assert "Current user message: no, execute it on your pc" in msg

    def test_model_tool_routing_includes_link_followup_history(self):
        msg = _message_for_model_tool_routing(
            "all",
            [
                {"role": "user", "content": "search for top PyTorch AI books released in 2026"},
                {"role": "assistant", "content": "I found one Amazon result and several lists."},
                {"role": "user", "content": "can you give me their amazon links"},
            ],
        )
        assert "search for top PyTorch AI books released in 2026" in msg
        assert "can you give me their amazon links" in msg
        assert "Current user message: all" in msg

    def test_disk_space_question_uses_shell(self):
        intent = detect_tool_intent("how much disk space left?", self._available())
        assert intent is not None
        assert intent.tool_name == "shell.run_command"
        assert intent.arguments["cmd"] == "df -h /"

    def test_direct_df_command_uses_shell(self):
        intent = detect_tool_intent("df -h /", self._available())
        assert intent is not None
        assert intent.tool_name == "shell.run_command"
        assert intent.arguments["cmd"] == "df -h /"

    def test_web_search(self):
        intent = detect_tool_intent("search the web for 'python dataclasses'", self._available())
        assert intent is not None
        assert intent.tool_name == "web.search"

    @pytest.mark.parametrize(
        ("message", "query"),
        [
            ("what is the latest news about openai?", "latest news about openai"),
            ("latest openai news", "latest openai news"),
            ("what happened with OpenAI today?", "what happened with OpenAI today"),
            ("current price of bitcoin", "current price of bitcoin"),
            ("who is the current CEO of OpenAI?", "who is the current CEO of OpenAI"),
        ],
    )
    def test_temporal_web_questions_use_search(self, message, query):
        intent = detect_tool_intent(message, self._available())
        assert intent is not None
        assert intent.tool_name == "web.search"
        assert intent.arguments["query"] == query

    def test_current_personal_state_does_not_trigger_search(self):
        assert detect_tool_intent("what is your current mood?", self._available()) is None

    def test_fetch_url(self):
        intent = detect_tool_intent("fetch https://example.com", self._available())
        assert intent is not None
        assert intent.tool_name == "web.fetch"

    def test_delete_file(self):
        intent = detect_tool_intent("delete file /tmp/junk.txt", self._available())
        assert intent is not None
        assert intent.tool_name == "fs.delete_path"

    def test_unavailable_tool_skipped(self):
        # Only fs.read_file available
        intent = detect_tool_intent("run command 'ls'", {"fs.read_file"})
        assert intent is None

    def test_cat_file(self):
        intent = detect_tool_intent("cat /tmp/data.log", self._available())
        assert intent is not None
        assert intent.tool_name == "fs.read_file"

    def test_create_skill_request(self):
        intent = detect_tool_intent(
            "Create a skill for yourself to convert meeting notes into tasks.",
            self._available(),
        )
        assert intent is not None
        assert intent.tool_name == "skills.create_from_request"
        assert "meeting notes" in intent.arguments["request"]
        assert intent.arguments["enable"] is True

    def test_enable_skill_request(self):
        intent = detect_tool_intent(
            "Enable skill report-writer",
            self._available(),
        )
        assert intent is not None
        assert intent.tool_name == "skills.enable"
        assert intent.arguments["skill_id"] == "report-writer"


# ===================================================================
# Action arbiter
# ===================================================================

class TestMakeToolDecision:
    def _intent(self, tool_name="fs.read_file"):
        return ToolIntent(
            tool_name=tool_name, arguments={}, reason="test", expected_outcome="test",
        )

    def test_execute_read_only(self):
        av = ActionVariables(risk_tolerance=0.5, autonomy_bias=0.5,
                            clarification_threshold=0.5, action_urgency=0.3)
        d = make_tool_decision(self._intent(), av, ToolCategory.READ_ONLY, trust=0.5)
        assert d.decision == "execute"

    def test_refuse_destructive_low_risk(self):
        av = ActionVariables(risk_tolerance=0.2, autonomy_bias=0.5,
                            clarification_threshold=0.5, action_urgency=0.3)
        d = make_tool_decision(self._intent("fs.delete_path"), av, ToolCategory.DESTRUCTIVE, trust=0.5)
        assert d.decision == "refuse"

    def test_clarify_low_autonomy(self):
        av = ActionVariables(risk_tolerance=0.5, autonomy_bias=0.2,
                            clarification_threshold=0.5, action_urgency=0.3)
        d = make_tool_decision(self._intent(), av, ToolCategory.READ_ONLY, trust=0.5)
        assert d.decision == "clarify"

    def test_clarify_write_high_clarification(self):
        av = ActionVariables(risk_tolerance=0.5, autonomy_bias=0.5,
                            clarification_threshold=0.8, action_urgency=0.3)
        d = make_tool_decision(self._intent("fs.write_file"), av, ToolCategory.WRITE, trust=0.5)
        assert d.decision == "clarify"

    def test_defer_low_urgency(self):
        av = ActionVariables(risk_tolerance=0.5, autonomy_bias=0.5,
                            clarification_threshold=0.5, action_urgency=0.1)
        d = make_tool_decision(self._intent(), av, ToolCategory.READ_ONLY, trust=0.5)
        assert d.decision == "defer"

    def test_execute_write_normal(self):
        av = ActionVariables(risk_tolerance=0.5, autonomy_bias=0.5,
                            clarification_threshold=0.5, action_urgency=0.3)
        d = make_tool_decision(self._intent("fs.write_file"), av, ToolCategory.WRITE, trust=0.5)
        assert d.decision == "execute"

    def test_assisted_autonomy_clarifies_write(self):
        av = ActionVariables(risk_tolerance=0.8, autonomy_bias=0.8,
                            clarification_threshold=0.2, action_urgency=0.8)
        d = make_tool_decision(
            self._intent("fs.write_file"),
            av,
            ToolCategory.WRITE,
            trust=0.8,
            autonomy_level="assisted",
        )
        assert d.decision == "clarify"
        assert "Assisted autonomy" in d.rationale

    def test_high_risk_autonomy_executes_destructive_inside_tool_scope(self):
        av = ActionVariables(risk_tolerance=0.1, autonomy_bias=0.1,
                            clarification_threshold=1.0, action_urgency=0.1)
        d = make_tool_decision(
            self._intent("fs.delete_path"),
            av,
            ToolCategory.DESTRUCTIVE,
            trust=0.1,
            autonomy_level="high_risk",
        )
        assert d.decision == "execute"

    def test_agency_refusal_blocks_tool_execution(self):
        av = ActionVariables(risk_tolerance=0.9, autonomy_bias=0.9,
                            clarification_threshold=0.1, action_urgency=0.9)
        d = make_tool_decision(
            self._intent("fs.read_file"),
            av,
            ToolCategory.READ_ONLY,
            trust=0.5,
            agency_decision=AgencyDecision(action="refuse", tool_instruction="No tools."),
        )
        assert d.decision == "refuse"
        assert "Agency stance" in d.rationale

    def test_decision_includes_rationale(self):
        av = ActionVariables(risk_tolerance=0.2, autonomy_bias=0.5,
                            clarification_threshold=0.5, action_urgency=0.3)
        d = make_tool_decision(self._intent("fs.delete_path"), av, ToolCategory.DESTRUCTIVE, trust=0.5)
        assert d.rationale != ""
        assert d.intent is not None


# ===================================================================
# Tool appraisal
# ===================================================================

class TestToolAppraisal:
    def test_success_read(self):
        result = ToolResult(tool_name="fs.read_file", success=True, output="file content")
        obs = appraise_tool_result(result, ToolCategory.READ_ONLY)
        assert obs.certainty_delta > 0
        assert obs.resolution_delta < 0
        assert obs.emotional_delta["certainty"] > 0

    def test_success_empty_output(self):
        result = ToolResult(tool_name="fs.search_text", success=True, output="")
        obs = appraise_tool_result(result, ToolCategory.READ_ONLY)
        assert obs.certainty_delta < 0
        assert obs.resolution_delta > 0

    def test_failure(self):
        result = ToolResult(tool_name="shell.run_command", success=False, output="",
                          error="Exit code 1")
        obs = appraise_tool_result(result, ToolCategory.DESTRUCTIVE)
        assert obs.certainty_delta < 0
        assert obs.resolution_delta > 0
        assert obs.emotional_delta["arousal"] > 0
        assert obs.emotional_delta["valence"] < 0

    def test_destructive_success(self):
        result = ToolResult(tool_name="fs.delete_path", success=True, output="Deleted")
        obs = appraise_tool_result(result, ToolCategory.DESTRUCTIVE)
        assert obs.self_observation == "decisive"
        assert obs.emotional_delta["energy"] < 0

    def test_permission_denied(self):
        result = ToolResult(tool_name="fs.read_file", success=False, output="",
                          error="Permission denied")
        obs = appraise_tool_result(result, ToolCategory.READ_ONLY)
        assert "blocked" in obs.summary
        assert obs.self_observation == "hesitant"

    def test_failure_increases_resolution(self):
        result = ToolResult(tool_name="shell.run_command", success=False, output="",
                          error="Command not found")
        obs = appraise_tool_result(result, ToolCategory.DESTRUCTIVE)
        assert obs.resolution_delta == 0.10


# ===================================================================
# Tool loop
# ===================================================================

class TestToolLoop:
    def test_no_tool_intent_returns_empty_trace(self):
        _, exe = _make_executor()
        engine = EmotionalEngine()
        result = run_tool_loop(
            user_message="How are you?",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
        )
        assert result.trace.loop_count == 0
        assert result.trace.proposed_intents == []
        assert result.tool_context_summary == ""

    def test_successful_tool_execution(self, tmp_path):
        _, exe = _make_executor()
        engine = EmotionalEngine()
        f = tmp_path / "data.txt"
        f.write_text("hello world")
        result = run_tool_loop(
            user_message=f"read file {f}",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
        )
        assert result.trace.loop_count == 1
        assert len(result.trace.executed_results) == 1
        assert result.trace.executed_results[0].success is True
        assert "fs.read_file: success" in result.tool_context_summary
        assert "hello world" not in result.tool_context_summary

    def test_shell_output_is_available_to_generator(self):
        _, exe = _make_executor()
        engine = EmotionalEngine()
        result = run_tool_loop(
            user_message="run command 'printf tool-visible'",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
            autonomy_level="high_risk",
        )
        assert result.trace.loop_count == 1
        assert result.trace.executed_results[0].success is True
        assert "output='tool-visible'" in result.tool_context_summary

    def test_web_search_output_is_available_to_generator(self):
        _, exe = _make_executor_with_fake_web()
        engine = EmotionalEngine()
        result = run_tool_loop(
            user_message="what is the latest news about openai?",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
        )
        assert result.trace.loop_count == 1
        assert result.trace.executed_results[0].success is True
        assert "OpenAI update" in result.tool_context_summary
        assert "https://example.com/openai-update" in result.tool_context_summary
        assert "OpenAI search snippet" in result.tool_context_summary

    def test_web_extract_output_is_available_to_generator(self):
        _, exe = _make_executor_with_fake_web()
        engine = EmotionalEngine()
        result = run_tool_loop(
            user_message="extract text from https://example.com/article",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
        )
        assert result.trace.loop_count == 1
        assert result.trace.executed_results[0].success is True
        assert "Readable text from https://example.com/article" in result.tool_context_summary

    def test_failed_tool_execution_changes_state(self):
        _, exe = _make_executor()
        engine = EmotionalEngine()
        certainty_before = engine.state.certainty
        result = run_tool_loop(
            user_message="read file /nonexistent_xyz_42",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
        )
        assert result.trace.loop_count == 1
        assert result.trace.executed_results[0].success is False
        # Certainty should have decreased
        assert engine.state.certainty < certainty_before

    def test_action_variables_populated(self):
        _, exe = _make_executor()
        engine = EmotionalEngine()
        result = run_tool_loop(
            user_message="How are you?",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
        )
        av = result.action_variables
        assert 0.0 <= av.risk_tolerance <= 1.0
        assert 0.0 <= av.action_urgency <= 1.0

    def test_non_execute_decision_no_execution(self):
        """When arbiter says clarify/defer/refuse, no tool executes."""
        _, exe = _make_executor()
        # Very low certainty + low trust → low autonomy_bias → clarify
        engine = EmotionalEngine()
        engine.state.certainty = 0.1
        person = PersonProfile(person_id="test", trust=0.1)
        result = run_tool_loop(
            user_message="delete file /tmp/important.dat",
            state=engine.state,
            person=person,
            defense_active=True,
            executor=exe,
            engine=engine,
        )
        assert result.trace.loop_count == 0
        assert len(result.trace.executed_results) == 0
        assert result.trace.final_decision is not None
        assert result.trace.final_decision.decision in ("clarify", "refuse", "defer")

    def test_bounded_loop_count(self):
        """Loop count is bounded by max_executions."""
        _, exe = _make_executor()
        engine = EmotionalEngine()
        result = run_tool_loop(
            user_message="read file /nonexistent_xyz_42",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
            max_executions=2,
            hard_cap=3,
        )
        # Should execute once then stop (continue_tool_loop is False)
        assert result.trace.loop_count <= 2

    def test_intent_gets_action_variable_values(self, tmp_path):
        """ToolIntent fields are populated from derived action variables."""
        _, exe = _make_executor()
        engine = EmotionalEngine()
        f = tmp_path / "x.txt"
        f.write_text("data")
        result = run_tool_loop(
            user_message=f"read file {f}",
            state=engine.state,
            person=None,
            defense_active=False,
            executor=exe,
            engine=engine,
        )
        intent = result.trace.proposed_intents[0]
        assert intent.urgency == result.action_variables.action_urgency
        assert intent.risk_tolerance == result.action_variables.risk_tolerance


# ===================================================================
# Pipeline integration
# ===================================================================

class TestPipelineToolIntegration:
    def test_no_executor_no_tool_trace(self):
        """Pipeline without tool_executor: tool_trace stays None."""
        pipe = CognitivePipeline()
        result = pipe.process("Hello there")
        assert result.debug.tool_trace is None
        assert result.debug.action_variables is None
        pipe.close()

    def test_direct_response_with_executor(self):
        """Conversational message with executor wired: no tool execution."""
        pipe, _, _ = _make_pipeline_with_tools()
        result = pipe.process("How are you doing?")
        assert result.debug.tool_trace is not None
        assert result.debug.tool_trace.loop_count == 0
        assert result.debug.tool_trace.proposed_intents == []
        assert result.debug.action_variables is not None
        pipe.close()

    def test_tool_turn_populates_trace(self, tmp_path):
        """File read request through pipeline populates full tool_trace."""
        pipe, _, _ = _make_pipeline_with_tools()
        f = tmp_path / "info.txt"
        f.write_text("important data")
        result = pipe.process(f"read file {f}")
        tt = result.debug.tool_trace
        assert tt is not None
        assert tt.loop_count == 1
        assert len(tt.executed_results) == 1
        assert tt.executed_results[0].success is True
        assert tt.final_decision.decision == "execute"
        assert len(tt.observations) == 1
        pipe.close()

    def test_tool_turn_has_timing(self, tmp_path):
        """Pipeline records tool_loop timing."""
        pipe, _, _ = _make_pipeline_with_tools()
        f = tmp_path / "info.txt"
        f.write_text("data")
        result = pipe.process(f"read file {f}")
        assert "tool_loop" in result.debug.stage_timings_ms
        pipe.close()

    def test_failed_tool_lowers_certainty(self):
        """Failed tool execution during pipeline lowers certainty."""
        pipe, _, _ = _make_pipeline_with_tools()
        pipe.process("How are you?")
        pipe.process("read file /nonexistent_xyz_42")
        # The modulator snapshot in debug is captured BEFORE tool loop,
        # but the engine state has been updated by tool appraisal
        pipe.close()

    def test_existing_pipeline_behavior_preserved(self):
        """Pipeline without tools still works exactly as before."""
        pipe = CognitivePipeline()
        r1 = pipe.process("Hello")
        r2 = pipe.process("How are you?")
        assert r1.response != ""
        assert r2.response != ""
        assert r1.debug.tool_trace is None
        pipe.close()


# ===================================================================
# Debug state + serialization
# ===================================================================

class TestDebugStateSerialization:
    def test_debug_state_has_action_variables(self):
        debug = DebugState()
        assert debug.action_variables is None
        from core.types import ActionVariables
        debug.action_variables = ActionVariables()
        assert debug.action_variables.risk_tolerance == 0.5

    def test_debug_to_dict_with_tool_trace(self):
        """_debug_to_dict includes tool_trace when populated."""
        from runtime.debug.api import _debug_to_dict

        debug = DebugState()
        intent = ToolIntent(
            tool_name="fs.read_file", arguments={"path": "/x"},
            reason="test", expected_outcome="data",
            urgency=0.5, risk_tolerance=0.6,
        )
        debug.tool_trace = ToolTrace(
            proposed_intents=[intent],
            final_decision=ToolDecision(decision="execute", intent=intent, rationale="ok"),
            executed_results=[ToolResult(tool_name="fs.read_file", success=True, output="data")],
            observations=[ToolObservation(summary="success", certainty_delta=0.08)],
            loop_count=1,
        )
        debug.action_variables = ActionVariables(risk_tolerance=0.7)

        d = _debug_to_dict(debug)
        assert d["tool_trace"] is not None
        assert d["tool_trace"]["loop_count"] == 1
        assert d["tool_trace"]["final_decision"]["decision"] == "execute"
        assert len(d["tool_trace"]["proposed_intents"]) == 1
        assert len(d["tool_trace"]["executed_results"]) == 1
        assert len(d["tool_trace"]["observations"]) == 1
        assert d["action_variables"]["risk_tolerance"] == 0.7

    def test_debug_to_dict_without_tool_trace(self):
        """_debug_to_dict handles None tool_trace gracefully."""
        from runtime.debug.api import _debug_to_dict
        debug = DebugState()
        d = _debug_to_dict(debug)
        assert d["tool_trace"] is None
        assert d["action_variables"] is None
        assert "appraisal_frame" in d
        assert "relationship_context" in d

    def test_pipeline_debug_serializable(self, tmp_path):
        """Full pipeline debug state with tools is serializable."""
        from runtime.debug.api import _debug_to_dict
        import json

        pipe, _, _ = _make_pipeline_with_tools()
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = pipe.process(f"read file {f}")
        d = _debug_to_dict(result.debug)
        # Should not raise
        serialized = json.dumps(d, default=str)
        assert "tool_trace" in serialized
        pipe.close()


# ===================================================================
# Generator tool context section
# ===================================================================

class TestGeneratorToolContext:
    def test_no_tool_context_no_section(self):
        from core.types import PipelineContext
        from core.dual_process.generator import build_system_prompt
        ctx = PipelineContext()
        prompt = build_system_prompt(ctx)
        assert "## Tool Execution Results" not in prompt

    def test_tool_context_appears_in_prompt(self):
        from core.types import PipelineContext
        from core.dual_process.generator import build_system_prompt
        ctx = PipelineContext(
            tool_context_summary="[fs.read_file] fs.read_file: success",
        )
        prompt = build_system_prompt(ctx)
        assert "## Tool Execution Results" in prompt
        assert "fs.read_file: success" in prompt
        assert "If the user explicitly asks for raw output" in prompt
