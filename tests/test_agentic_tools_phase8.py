"""Tests for Agentic Tools Phase 8: Proactive and autonomous behavior."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from core.types import (
    ActionVariables,
    ModulatorState,
    PersonProfile,
    ProactiveAction,
    ProactiveTrace,
    ProactiveTrigger,
    ProactiveTriggerSource,
    TaskPlan,
    TaskStatus,
    TaskStep,
    ToolTrace,
    UnresolvedItem,
)
from core.proactive import (
    DEFAULT_ACTIVATION_THRESHOLD,
    DEFAULT_COOLDOWN,
    DEFAULT_IDLE_THRESHOLD,
    DEFAULT_MAX_PROACTIVE,
    _collect_triggers,
    _score_trigger,
    _select_action,
    evaluate_proactive,
)


# ===================================================================
# Helpers
# ===================================================================

def _make_state(**overrides) -> ModulatorState:
    kw = dict(arousal=0.5, valence=0.5, certainty=0.5,
              bonding=0.5, energy=0.7, resolution=0.0)
    kw.update(overrides)
    return ModulatorState(**kw)


def _make_person(**overrides) -> PersonProfile:
    kw = dict(person_id="u1", trust=0.6)
    kw.update(overrides)
    return PersonProfile(**kw)


def _make_unresolved(
    source: str = "contradiction",
    intensity: float = 0.5,
    item_id: str = "item_1",
    description: str = "Test unresolved item",
) -> UnresolvedItem:
    return UnresolvedItem(
        id=item_id,
        source=source,
        description=description,
        created_at=datetime.now(timezone.utc),
        intensity=intensity,
        decay_rate=0.05,
    )


def _make_plan(
    goal: str = "test plan",
    pending_steps: int = 2,
    status: TaskStatus = TaskStatus.IN_PROGRESS,
) -> TaskPlan:
    steps = [
        TaskStep(
            id=f"s{i}",
            tool_name="fs.list_dir",
            arguments={"path": "/tmp"},
            description=f"step {i}",
            status=TaskStatus.PENDING if i < pending_steps else TaskStatus.COMPLETED,
        )
        for i in range(max(pending_steps, 1))
    ]
    return TaskPlan(id="plan_1", goal=goal, steps=steps, status=status)


# ===================================================================
# Type tests
# ===================================================================

class TestProactiveTypes:
    def test_trigger_source_values(self):
        assert ProactiveTriggerSource.UNRESOLVED_ITEM.value == "unresolved_item"
        assert ProactiveTriggerSource.PENDING_TASK.value == "pending_task"
        assert ProactiveTriggerSource.COMMITMENT.value == "commitment"
        assert ProactiveTriggerSource.TEMPORAL.value == "temporal"
        assert ProactiveTriggerSource.EMOTIONAL_SALIENCE.value == "emotional_salience"

    def test_trigger_intensity_clamped(self):
        t = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test",
            intensity=1.5,
        )
        assert t.intensity == 1.0
        t2 = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test",
            intensity=-0.5,
        )
        assert t2.intensity == 0.0

    def test_proactive_action_fields(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.COMMITMENT,
            description="follow up",
            intensity=0.5,
        )
        action = ProactiveAction(
            action_type="follow_up",
            trigger=trigger,
            message="I should follow up",
            rationale="commitment pending",
        )
        assert action.action_type == "follow_up"
        assert action.trigger is trigger

    def test_proactive_trace_defaults(self):
        trace = ProactiveTrace()
        assert trace.triggers_found == []
        assert trace.action_taken is None
        assert trace.suppressed_reasons == []
        assert trace.idle_seconds == 0.0
        assert trace.proactive_count == 0


# ===================================================================
# Trigger collection
# ===================================================================

class TestCollectTriggers:
    def test_no_triggers_when_nothing_unresolved(self):
        triggers = _collect_triggers([], None, 0.0, _make_state())
        assert triggers == []

    def test_unresolved_item_above_threshold(self):
        item = _make_unresolved(intensity=0.5)
        triggers = _collect_triggers([item], None, 0.0, _make_state())
        sources = [t.source for t in triggers]
        assert ProactiveTriggerSource.UNRESOLVED_ITEM in sources

    def test_unresolved_item_below_threshold_ignored(self):
        item = _make_unresolved(intensity=0.2)
        triggers = _collect_triggers([item], None, 0.0, _make_state())
        unresolved_triggers = [
            t for t in triggers
            if t.source == ProactiveTriggerSource.UNRESOLVED_ITEM
        ]
        assert unresolved_triggers == []

    def test_pending_task_creates_trigger(self):
        plan = _make_plan(pending_steps=3)
        triggers = _collect_triggers([], plan, 0.0, _make_state())
        task_triggers = [
            t for t in triggers
            if t.source == ProactiveTriggerSource.PENDING_TASK
        ]
        assert len(task_triggers) == 1
        assert "test plan" in task_triggers[0].description

    def test_completed_plan_no_trigger(self):
        plan = _make_plan(status=TaskStatus.COMPLETED)
        triggers = _collect_triggers([], plan, 0.0, _make_state())
        task_triggers = [
            t for t in triggers
            if t.source == ProactiveTriggerSource.PENDING_TASK
        ]
        assert task_triggers == []

    def test_commitment_creates_trigger(self):
        item = _make_unresolved(source="commitment", intensity=0.3)
        triggers = _collect_triggers([item], None, 0.0, _make_state())
        commit_triggers = [
            t for t in triggers
            if t.source == ProactiveTriggerSource.COMMITMENT
        ]
        assert len(commit_triggers) == 1

    def test_temporal_trigger_when_idle_with_resolution(self):
        state = _make_state(resolution=0.5)
        triggers = _collect_triggers(
            [], None, DEFAULT_IDLE_THRESHOLD + 100, state,
        )
        temporal = [
            t for t in triggers
            if t.source == ProactiveTriggerSource.TEMPORAL
        ]
        assert len(temporal) == 1

    def test_no_temporal_when_resolution_low(self):
        state = _make_state(resolution=0.1)
        triggers = _collect_triggers(
            [], None, DEFAULT_IDLE_THRESHOLD + 100, state,
        )
        temporal = [
            t for t in triggers
            if t.source == ProactiveTriggerSource.TEMPORAL
        ]
        assert temporal == []

    def test_emotional_salience_trigger(self):
        state = _make_state(resolution=0.6, arousal=0.5)
        triggers = _collect_triggers([], None, 0.0, state)
        emotional = [
            t for t in triggers
            if t.source == ProactiveTriggerSource.EMOTIONAL_SALIENCE
        ]
        assert len(emotional) == 1


# ===================================================================
# Trigger scoring
# ===================================================================

class TestScoreTrigger:
    def test_base_score_is_intensity(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test", intensity=0.5,
        )
        score = _score_trigger(trigger, _make_state(), _make_person())
        # Score should be close to 0.5 with neutral state
        assert 0.3 <= score <= 0.7

    def test_high_resolution_boosts_score(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test", intensity=0.5,
        )
        low_res = _score_trigger(trigger, _make_state(resolution=0.2), _make_person())
        high_res = _score_trigger(trigger, _make_state(resolution=0.6), _make_person())
        assert high_res > low_res

    def test_low_energy_reduces_score(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test", intensity=0.5,
        )
        high_energy = _score_trigger(trigger, _make_state(energy=0.8), _make_person())
        low_energy = _score_trigger(trigger, _make_state(energy=0.2), _make_person())
        assert high_energy > low_energy

    def test_high_trust_boosts_score(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test", intensity=0.5,
        )
        low_trust = _score_trigger(trigger, _make_state(), _make_person(trust=0.3))
        high_trust = _score_trigger(trigger, _make_state(), _make_person(trust=0.8))
        assert high_trust > low_trust

    def test_very_high_arousal_suppresses(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test", intensity=0.5,
        )
        moderate = _score_trigger(trigger, _make_state(arousal=0.5), _make_person())
        very_high = _score_trigger(trigger, _make_state(arousal=0.9), _make_person())
        assert moderate > very_high

    def test_score_clamped_0_1(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test", intensity=0.95,
        )
        score = _score_trigger(
            trigger, _make_state(resolution=0.9), _make_person(trust=0.9),
        )
        assert 0.0 <= score <= 1.0


# ===================================================================
# Action selection
# ===================================================================

class TestSelectAction:
    def test_pending_task_becomes_continue(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.PENDING_TASK,
            description="plan", intensity=0.5, item_id="plan_1",
        )
        plan = _make_plan()
        action = _select_action(trigger, plan, _make_state())
        assert action.action_type == "continue_task"

    def test_commitment_becomes_follow_up(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.COMMITMENT,
            description="promised to check", intensity=0.5,
        )
        action = _select_action(trigger, None, _make_state())
        assert action.action_type == "follow_up"

    def test_unresolved_becomes_follow_up(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="tension", intensity=0.5,
        )
        action = _select_action(trigger, None, _make_state())
        assert action.action_type == "follow_up"

    def test_temporal_becomes_suggest(self):
        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.TEMPORAL,
            description="idle", intensity=0.5,
        )
        action = _select_action(trigger, None, _make_state())
        assert action.action_type == "suggest"


# ===================================================================
# Full evaluation
# ===================================================================

class TestEvaluateProactive:
    def test_no_action_when_nothing_unresolved(self):
        action, trace = evaluate_proactive(
            state=_make_state(),
            unresolved_items=[],
            active_plan=None,
            person=_make_person(),
            idle_seconds=600.0,
            proactive_count=0,
        )
        assert action is None
        assert "No triggers found" in trace.suppressed_reasons

    def test_no_action_when_not_idle_enough(self):
        item = _make_unresolved(intensity=0.6)
        action, trace = evaluate_proactive(
            state=_make_state(),
            unresolved_items=[item],
            active_plan=None,
            person=_make_person(),
            idle_seconds=10.0,  # not idle enough
            proactive_count=0,
        )
        assert action is None
        assert any("Not idle" in r for r in trace.suppressed_reasons)

    def test_no_action_when_max_reached(self):
        item = _make_unresolved(intensity=0.6)
        action, trace = evaluate_proactive(
            state=_make_state(),
            unresolved_items=[item],
            active_plan=None,
            person=_make_person(),
            idle_seconds=600.0,
            proactive_count=3,
            max_proactive=3,
        )
        assert action is None
        assert any("Max proactive" in r for r in trace.suppressed_reasons)

    def test_no_action_during_cooldown(self):
        item = _make_unresolved(intensity=0.6)
        action, trace = evaluate_proactive(
            state=_make_state(),
            unresolved_items=[item],
            active_plan=None,
            person=_make_person(),
            idle_seconds=600.0,
            proactive_count=1,
            last_proactive_at=time.time() - 60.0,  # only 60s ago
            cooldown=300.0,
        )
        assert action is None
        assert any("Cooldown" in r for r in trace.suppressed_reasons)

    def test_no_action_when_energy_too_low(self):
        item = _make_unresolved(intensity=0.6)
        action, trace = evaluate_proactive(
            state=_make_state(energy=0.1),
            unresolved_items=[item],
            active_plan=None,
            person=_make_person(),
            idle_seconds=600.0,
            proactive_count=0,
        )
        assert action is None
        assert any("Energy" in r for r in trace.suppressed_reasons)

    def test_unresolved_item_triggers_action(self):
        item = _make_unresolved(intensity=0.6)
        action, trace = evaluate_proactive(
            state=_make_state(resolution=0.5),
            unresolved_items=[item],
            active_plan=None,
            person=_make_person(trust=0.7),
            idle_seconds=600.0,
            proactive_count=0,
        )
        assert action is not None
        assert action.action_type == "follow_up"
        assert trace.action_taken is action
        assert len(trace.triggers_found) >= 1

    def test_pending_task_triggers_continue(self):
        plan = _make_plan(pending_steps=2)
        action, trace = evaluate_proactive(
            state=_make_state(resolution=0.3),
            unresolved_items=[],
            active_plan=plan,
            person=_make_person(),
            idle_seconds=600.0,
            proactive_count=0,
        )
        assert action is not None
        assert action.action_type == "continue_task"

    def test_limits_recorded_in_trace(self):
        _, trace = evaluate_proactive(
            state=_make_state(),
            unresolved_items=[],
            active_plan=None,
            person=_make_person(),
            idle_seconds=600.0,
            proactive_count=0,
            max_proactive=5,
            idle_threshold=200.0,
            cooldown=100.0,
        )
        assert trace.limits_applied["max_proactive"] == 5
        assert trace.limits_applied["idle_threshold"] == 200.0
        assert trace.limits_applied["cooldown"] == 100.0

    def test_trigger_below_activation_threshold(self):
        """Low-intensity trigger doesn't activate."""
        item = _make_unresolved(intensity=0.3)
        action, trace = evaluate_proactive(
            state=_make_state(resolution=0.0, energy=0.5),
            unresolved_items=[item],
            active_plan=None,
            person=_make_person(trust=0.3),
            idle_seconds=600.0,
            proactive_count=0,
            activation_threshold=0.5,
        )
        assert action is None
        assert any("threshold" in r.lower() for r in trace.suppressed_reasons)

    def test_best_trigger_selected(self):
        """When multiple triggers exist, the highest-scored one is selected."""
        low = _make_unresolved(intensity=0.35, item_id="low", description="low")
        high = _make_unresolved(intensity=0.7, item_id="high", description="high priority")
        action, trace = evaluate_proactive(
            state=_make_state(resolution=0.5),
            unresolved_items=[low, high],
            active_plan=None,
            person=_make_person(trust=0.7),
            idle_seconds=600.0,
            proactive_count=0,
        )
        assert action is not None
        assert "high" in action.trigger.description.lower()


# ===================================================================
# Pipeline integration
# ===================================================================

class TestPipelineProactive:
    def test_pipeline_has_proactive_tracking(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        assert p._proactive_count == 0
        assert p._last_proactive_at is None

    def test_end_session_clears_proactive_state(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        p._proactive_count = 5
        p._last_proactive_at = time.time()
        p.end_session()
        assert p._proactive_count == 0
        assert p._last_proactive_at is None

    def test_process_proactive_returns_none_when_quiet(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        # No unresolved items, no plan, fresh pipeline → nothing to do
        result = p.process_proactive("default", idle_threshold=0.0)
        assert result is None

    def test_process_proactive_with_unresolved_triggers(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        # Simulate idle time
        p._last_turn_time = time.time() - 600.0
        # Add an unresolved item with high intensity
        p.engine.add_unresolved(UnresolvedItem(
            id="test_1",
            source="contradiction",
            description="Something we need to address",
            created_at=datetime.now(timezone.utc),
            intensity=0.7,
            decay_rate=0.02,
        ))
        # Boost resolution so triggers fire
        p.engine.state.resolution = 0.5

        result = p.process_proactive(
            "default",
            idle_threshold=0.0,  # bypass idle check for test
            cooldown=0.0,        # bypass cooldown
        )
        assert result is not None
        assert result.response  # generator produced something
        assert result.debug.proactive_trace is not None
        assert result.debug.proactive_trace.action_taken is not None
        assert p._proactive_count == 1
        assert p._last_proactive_at is not None

    def test_process_proactive_increments_count(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        p._last_turn_time = time.time() - 600.0
        p.engine.add_unresolved(UnresolvedItem(
            id="test_1", source="commitment",
            description="Follow up on promise",
            created_at=datetime.now(timezone.utc),
            intensity=0.6, decay_rate=0.0,
        ))
        p.engine.state.resolution = 0.5

        r1 = p.process_proactive("default", idle_threshold=0.0, cooldown=0.0)
        assert r1 is not None
        assert p._proactive_count == 1

        r2 = p.process_proactive("default", idle_threshold=0.0, cooldown=0.0)
        if r2 is not None:
            assert p._proactive_count == 2

    def test_process_proactive_respects_max(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        p._last_turn_time = time.time() - 600.0
        p._proactive_count = 3  # already at max
        p.engine.add_unresolved(UnresolvedItem(
            id="test_1", source="contradiction",
            description="Unresolved item",
            created_at=datetime.now(timezone.utc),
            intensity=0.7, decay_rate=0.02,
        ))

        result = p.process_proactive(
            "default", max_proactive=3, idle_threshold=0.0, cooldown=0.0,
        )
        assert result is None

    def test_process_proactive_records_self_observation(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        p._last_turn_time = time.time() - 600.0
        p.engine.add_unresolved(UnresolvedItem(
            id="test_1", source="contradiction",
            description="Unresolved matter",
            created_at=datetime.now(timezone.utc),
            intensity=0.7, decay_rate=0.02,
        ))
        p.engine.state.resolution = 0.5

        result = p.process_proactive("default", idle_threshold=0.0, cooldown=0.0)
        assert result is not None
        # Check self-observation was recorded (proactive trait)
        profile = p.self_profile.get_profile()
        assert any("proactive" in t.lower() for t in profile.observed_traits)

    def test_debug_state_has_proactive_trace(self):
        from pipeline import DebugState
        d = DebugState()
        assert d.proactive_trace is None

    def test_normal_process_unchanged(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        result = p.process("hello")
        assert result.response
        assert result.debug.proactive_trace is None


# ===================================================================
# Debug API serialization
# ===================================================================

class TestDebugProactiveSerialization:
    def test_proactive_trace_serialized(self):
        from runtime.debug.api import _debug_to_dict
        from pipeline import DebugState

        trigger = ProactiveTrigger(
            source=ProactiveTriggerSource.UNRESOLVED_ITEM,
            description="test item", intensity=0.6, item_id="item_1",
        )
        action = ProactiveAction(
            action_type="follow_up", trigger=trigger,
            message="follow up message", rationale="high tension",
        )
        trace = ProactiveTrace(
            triggers_found=[trigger],
            action_taken=action,
            idle_seconds=500.0,
            proactive_count=1,
            limits_applied={"max_proactive": 3, "idle_threshold": 300.0,
                            "cooldown": 300.0, "activation_threshold": 0.4},
        )

        debug = DebugState()
        debug.proactive_trace = trace
        d = _debug_to_dict(debug)

        assert d["proactive_trace"] is not None
        assert d["proactive_trace"]["idle_seconds"] == 500.0
        assert d["proactive_trace"]["proactive_count"] == 1
        assert len(d["proactive_trace"]["triggers_found"]) == 1
        assert d["proactive_trace"]["triggers_found"][0]["source"] == "unresolved_item"
        assert d["proactive_trace"]["action_taken"]["action_type"] == "follow_up"
        assert d["proactive_trace"]["limits_applied"]["max_proactive"] == 3

    def test_no_proactive_trace_serialized_as_none(self):
        from runtime.debug.api import _debug_to_dict
        from pipeline import DebugState

        debug = DebugState()
        d = _debug_to_dict(debug)
        assert d["proactive_trace"] is None

    def test_suppressed_trace_serialized(self):
        from runtime.debug.api import _debug_to_dict
        from pipeline import DebugState

        trace = ProactiveTrace(
            suppressed_reasons=["Max proactive actions reached (3/3)"],
            idle_seconds=600.0,
            proactive_count=3,
            limits_applied={"max_proactive": 3},
        )
        debug = DebugState()
        debug.proactive_trace = trace
        d = _debug_to_dict(debug)

        assert d["proactive_trace"]["action_taken"] is None
        assert len(d["proactive_trace"]["suppressed_reasons"]) == 1


# ===================================================================
# Runtime config
# ===================================================================

class TestRuntimeConfig:
    def test_proactive_config_defaults(self):
        from runtime.config import RuntimeConfig
        cfg = RuntimeConfig()
        assert cfg.proactive_enabled is False
        assert cfg.proactive_idle_threshold == 300.0
        assert cfg.proactive_max_per_session == 3
        assert cfg.proactive_cooldown == 300.0
        assert cfg.proactive_check_interval == 60.0

    def test_proactive_config_from_yaml(self, tmp_path):
        from runtime.config import RuntimeConfig
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "proactive_enabled: true\n"
            "proactive_idle_threshold: 120.0\n"
            "proactive_max_per_session: 5\n"
        )
        cfg = RuntimeConfig.from_yaml(str(cfg_file))
        assert cfg.proactive_enabled is True
        assert cfg.proactive_idle_threshold == 120.0
        assert cfg.proactive_max_per_session == 5


# ===================================================================
# Session manager proactive
# ===================================================================

class TestSessionManagerProactive:
    def test_manager_accepts_proactive_callback(self):
        from runtime.config import RuntimeConfig
        from runtime.sessions.manager import SessionManager

        called = []

        async def cb(session_key, user_id, msg):
            called.append((session_key, user_id, msg))

        mgr = SessionManager(RuntimeConfig(), proactive_callback=cb)
        assert mgr._proactive_callback is cb


# ===================================================================
# Regression tests
# ===================================================================

class TestPhase8Regression:
    def test_existing_process_unchanged(self):
        """Normal message processing is not affected by proactive additions."""
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        result = p.process("tell me about the weather")
        assert result.response
        assert result.debug.proactive_trace is None

    def test_tool_trace_still_works(self, tmp_path):
        """Tool loop still works correctly with proactive additions."""
        from pipeline import CognitivePipeline
        from tools.registry import ToolRegistry
        from tools.executor import ToolExecutor
        from tools import register_builtins

        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)

        p = CognitivePipeline(tool_executor=exe)
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = p.process(f"read file {f}")
        assert result.debug.tool_trace is not None

    def test_builtin_count_unchanged(self):
        from tools.registry import ToolRegistry
        from tools.executor import ToolExecutor
        from tools import register_builtins
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)
        assert len(reg) == 18

    def test_end_session_still_works(self):
        from pipeline import CognitivePipeline
        p = CognitivePipeline()
        p._proactive_count = 2
        p.process("hello")
        result = p.end_session()
        assert p._proactive_count == 0
        assert p._conversation_history == []
        assert p._active_task_plan is None


# ===================================================================
# Fix tests — proactive runtime correctness (callback, serialization, decay)
# ===================================================================


class TestProactiveCallbackWiring:
    """Fix 1: JarvisApp must construct SessionManager with a real callback."""

    def test_jarvis_app_passes_callback(self):
        from runtime.app import JarvisApp
        from runtime.config import RuntimeConfig
        cfg = RuntimeConfig(llm_backend="mock", proactive_enabled=True)
        app = JarvisApp(config=cfg)
        assert app.session_manager._proactive_callback is not None

    def test_jarvis_app_callback_is_deliver_proactive(self):
        from runtime.app import JarvisApp
        from runtime.config import RuntimeConfig
        cfg = RuntimeConfig(llm_backend="mock")
        app = JarvisApp(config=cfg)
        cb = app.session_manager._proactive_callback
        assert cb.__func__ is JarvisApp._deliver_proactive
        assert cb.__self__ is app

    def test_deliver_proactive_console(self, capsys):
        """Console delivery prints to stdout."""
        import asyncio
        from runtime.app import JarvisApp
        from runtime.config import RuntimeConfig
        from runtime.channels.console import ConsoleChannel

        cfg = RuntimeConfig(llm_backend="mock", console_enabled=True)
        app = JarvisApp(config=cfg)
        # Fake a console channel being active
        app._console = object()  # truthy — delivery checks is not None

        asyncio.get_event_loop().run_until_complete(
            app._deliver_proactive("console:user:direct", "user", "hello proactive")
        )
        captured = capsys.readouterr()
        assert "hello proactive" in captured.out

    def test_deliver_proactive_bad_session_key(self, caplog):
        """Malformed session_key logs a warning, does not crash."""
        import asyncio
        from runtime.app import JarvisApp
        from runtime.config import RuntimeConfig

        cfg = RuntimeConfig(llm_backend="mock")
        app = JarvisApp(config=cfg)
        # No exception
        asyncio.get_event_loop().run_until_complete(
            app._deliver_proactive("bad_key", "user", "msg")
        )

    def test_deliver_proactive_no_channel(self, caplog):
        """Unknown platform logs warning, does not crash."""
        import asyncio
        from runtime.app import JarvisApp
        from runtime.config import RuntimeConfig

        cfg = RuntimeConfig(llm_backend="mock")
        app = JarvisApp(config=cfg)
        asyncio.get_event_loop().run_until_complete(
            app._deliver_proactive("unknown:user:chat", "user", "msg")
        )


class TestProactiveSerialization:
    """Fix 2: Proactive must acquire per-user lock, same as normal turns."""

    @pytest.fixture
    def _event_loop_policy(self):
        """Ensure a running event loop for asyncio tests."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        yield loop
        loop.close()

    def test_run_proactive_acquires_user_lock(self, _event_loop_policy, tmp_path):
        """_run_proactive must hold user_lock during pipeline execution."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from runtime.sessions.manager import SessionManager
        from runtime.config import RuntimeConfig

        loop = _event_loop_policy
        cfg = RuntimeConfig(
            data_dir=str(tmp_path),
            llm_backend="mock",
            proactive_enabled=True,
        )
        mgr = SessionManager(config=cfg)

        lock = asyncio.Lock()
        lock_acquired_during_process = False

        original_to_thread = asyncio.to_thread

        async def patched_to_thread(fn, *args, **kwargs):
            nonlocal lock_acquired_during_process
            lock_acquired_during_process = lock.locked()
            return None  # process_proactive returns None → no action

        # Create a fake session with the lock
        session = MagicMock()
        session._user_lock = lock
        session._processing = False
        session._queue = MagicMock()
        session._queue.empty.return_value = True
        session.pipeline = MagicMock()
        session.user_id = "user1"
        session.last_activity = 0.0  # very idle

        with patch("asyncio.to_thread", patched_to_thread):
            loop.run_until_complete(mgr._run_proactive("console:user1:dm", session))

        assert lock_acquired_during_process, "user_lock must be held during process_proactive"

    def test_no_proactive_during_processing(self, _event_loop_policy, tmp_path):
        """_proactive_sweep skips sessions with _processing=True."""
        import asyncio
        from unittest.mock import MagicMock
        from runtime.sessions.manager import SessionManager
        from runtime.config import RuntimeConfig

        loop = _event_loop_policy
        cfg = RuntimeConfig(
            data_dir=str(tmp_path),
            llm_backend="mock",
            proactive_enabled=True,
            proactive_idle_threshold=1.0,
        )
        mgr = SessionManager(config=cfg)

        session = MagicMock()
        session._processing = True  # busy
        session._queue = MagicMock()
        session._queue.empty.return_value = True
        session.last_activity = 0.0  # very idle

        mgr._sessions["console:user1:dm"] = session
        loop.run_until_complete(mgr._proactive_sweep())
        # _run_proactive should NOT have been called (no pipeline access)
        session.pipeline.process_proactive.assert_not_called()

    def test_no_proactive_with_queued_work(self, _event_loop_policy, tmp_path):
        """_proactive_sweep skips sessions with queued messages."""
        import asyncio
        from unittest.mock import MagicMock
        from runtime.sessions.manager import SessionManager
        from runtime.config import RuntimeConfig

        loop = _event_loop_policy
        cfg = RuntimeConfig(
            data_dir=str(tmp_path),
            llm_backend="mock",
            proactive_enabled=True,
            proactive_idle_threshold=1.0,
        )
        mgr = SessionManager(config=cfg)

        session = MagicMock()
        session._processing = False
        session._queue = MagicMock()
        session._queue.empty.return_value = False  # has queued work
        session.last_activity = 0.0

        mgr._sessions["console:user1:dm"] = session
        loop.run_until_complete(mgr._proactive_sweep())
        session.pipeline.process_proactive.assert_not_called()


class TestProactiveElapsedDecay:
    """Fix 3: process_proactive must decay engine state before evaluation."""

    def test_elapsed_decay_applied(self):
        """Engine.decay() is called before proactive evaluation."""
        from pipeline import CognitivePipeline

        p = CognitivePipeline()
        # Set up state: process a message to establish _last_turn_time
        p.process("hello")
        old_time = p._last_turn_time

        # Record initial arousal
        initial_arousal = p.engine.state.arousal

        # Fake elapsed time (simulate 600s idle)
        p._last_turn_time = time.time() - 600

        # Run proactive — should apply decay before evaluation
        result = p.process_proactive("user1", idle_threshold=1.0)

        # _last_turn_time should have been updated
        assert p._last_turn_time > old_time

    def test_elapsed_decay_changes_modulators(self):
        """Modulators should be decayed by elapsed time before proactive eval."""
        from pipeline import CognitivePipeline

        p = CognitivePipeline()
        p.process("hello")

        # Spike arousal and set a past _last_turn_time
        p.engine.state = ModulatorState(
            arousal=0.9, valence=0.3, certainty=0.5,
            bonding=0.5, energy=0.7, resolution=0.0,
        )
        p._last_turn_time = time.time() - 3600  # 1 hour ago

        arousal_before = p.engine.state.arousal
        p.process_proactive("user1", idle_threshold=1.0)
        arousal_after = p.engine.state.arousal

        # Arousal should have decayed toward resting point
        assert arousal_after < arousal_before, (
            f"Expected arousal to decay: {arousal_before} → {arousal_after}"
        )

    def test_last_turn_time_updated_on_proactive(self):
        """_last_turn_time is set even when no action is taken."""
        from pipeline import CognitivePipeline

        p = CognitivePipeline()
        p._last_turn_time = time.time() - 1000
        old = p._last_turn_time

        p.process_proactive("user1", idle_threshold=1.0)
        assert p._last_turn_time > old

    def test_first_proactive_no_decay_crash(self):
        """No crash if _last_turn_time is None (no prior turns)."""
        from pipeline import CognitivePipeline

        p = CognitivePipeline()
        assert p._last_turn_time is None
        # Should not raise
        result = p.process_proactive("user1", idle_threshold=0.0)
        # _last_turn_time should now be set
        assert p._last_turn_time is not None
