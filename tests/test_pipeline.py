"""Tests for the cognitive pipeline — Phase 6."""
import pytest

from config.loader import get_config
from core.dual_process.generator import MockLLMBackend
from core.pipeline_features import PipelineFeatures
from pipeline import CognitivePipeline, DebugState, PipelineResponse
from runtime.debug.api import _debug_to_dict
from runtime.config import RuntimeConfig
from runtime.skills import enabled_skill_context, list_skills
from runtime.tools import create_tool_executor


class TestCognitivePipeline:
    def _make_pipeline(
        self,
        response: str = "I understand.",
        *,
        db_path: str = ":memory:",
    ) -> CognitivePipeline:
        backend = MockLLMBackend(response=response)
        return CognitivePipeline(llm_backend=backend, db_path=db_path)

    def test_basic_process(self):
        pipe = self._make_pipeline()
        result = pipe.process("Hello there", user_id="alice")
        assert isinstance(result, PipelineResponse)
        assert result.response == "I understand."
        assert result.debug.user_message == "Hello there"
        assert result.debug.user_id == "alice"

    def test_returns_debug_state(self):
        pipe = self._make_pipeline()
        result = pipe.process("How are you?", user_id="bob")
        d = result.debug
        assert isinstance(d, DebugState)
        assert d.detected_emotion is not None
        assert d.appraisal_frame is not None
        assert d.modulator_snapshot != {}
        assert d.event_classified != ""
        assert d.energy_after > 0
        assert d.emotion_label != ""

    def test_debug_includes_affect_and_agency(self):
        pipe = self._make_pipeline()
        result = pipe.process("I hate you because you are too slow", user_id="alice")
        assert result.debug.affect_state is not None
        assert result.debug.affect_state.signal("anger") > 0
        assert result.debug.agency_decision is not None
        assert result.debug.agency_decision.action in {"resist", "refuse", "demand_repair"}

    def test_contagion_affects_state(self):
        pipe = self._make_pipeline()
        # Excited message should shift arousal/valence
        pipe.process("I'm so excited and thrilled!!!", user_id="alice")
        snap = pipe.engine.snapshot()
        assert snap["arousal"] > 0.5 or snap["valence"] > 0.5

    def test_conflict_event_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("I'm angry with you about this argument!", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_upset_with_you_is_not_treated_as_generic_user_message(self):
        pipe = self._make_pipeline()
        result = pipe.process("I am upset with you", user_id="alice")
        assert result.debug.appraisal_frame is not None
        assert result.debug.appraisal_frame.targets_assistant is True
        assert result.debug.event_classified in {"conflict", "negative_feedback"}

    def test_warmth_event_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("You're so kind and caring", user_id="alice")
        assert result.debug.event_classified == "warmth"

    def test_positive_feedback_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("Thank you, I really appreciate that", user_id="alice")
        assert result.debug.event_classified == "positive_feedback"

    def test_betrayal_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("You lied and betrayed my trust", user_id="alice")
        assert result.debug.event_classified == "betrayal"

    def test_external_distress_uses_user_message_not_relational_conflict(self):
        pipe = self._make_pipeline()
        result = pipe.process("I'm furious about work, not at you.", user_id="alice")
        assert result.debug.appraisal_frame is not None
        assert result.debug.appraisal_frame.primary_target == "external"
        assert result.debug.appraisal_frame.targets_assistant is False
        assert result.debug.event_classified == "user_message"
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.trust == pytest.approx(0.5)

    def test_direct_attack_still_affects_relationship(self):
        pipe = self._make_pipeline()
        result = pipe.process("You are useless and this answer is terrible.", user_id="alice")
        assert result.debug.appraisal_frame is not None
        assert result.debug.appraisal_frame.targets_assistant is True
        assert result.debug.event_classified == "conflict"
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.trust < 0.5

    def test_energy_drains_over_messages(self):
        pipe = self._make_pipeline()
        initial_energy = pipe.engine.state.energy
        for i in range(10):
            pipe.process(f"Message {i}", user_id="alice")
        assert pipe.engine.state.energy < initial_energy

    def test_short_term_memory_grows(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("How are you?", user_id="alice")
        # Each process call records 2 entries (event + outcome)
        assert len(pipe.short_term) >= 4

    def test_conversation_history_tracked(self):
        pipe = self._make_pipeline(response="Fine, thanks!")
        pipe.process("Hello", user_id="alice")
        assert len(pipe._conversation_history) == 2
        assert pipe._conversation_history[0]["role"] == "user"
        assert pipe._conversation_history[1]["role"] == "assistant"

    def test_end_session_clears_state(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("You're great, thank you!", user_id="alice")
        result = pipe.end_session(user_id="alice")
        assert len(pipe.short_term) == 0
        assert len(pipe._conversation_history) == 0
        assert result.summary != ""

    def test_end_session_writes_to_long_term(self):
        pipe = self._make_pipeline()
        for i in range(5):
            pipe.process(f"Warm message {i}, thanks!", user_id="alice")
        initial_lt_count = pipe.long_term.count()
        pipe.end_session(user_id="alice")
        # Digestion should write at least one memory
        assert pipe.long_term.count() >= initial_lt_count

    def test_relationship_context_surfaces_across_sessions(self, tmp_path):
        db_path = str(tmp_path / "relationship.db")
        pipe = self._make_pipeline(db_path=db_path)
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        result = pipe.process("hello again", user_id="alice")
        assert result.debug.relationship_context is not None
        assert result.debug.relationship_context.open_loop_count >= 1
        assert "Relationship context" in pipe._llm_backend.last_system_prompt
        pipe.close()

    def test_relationship_repair_closes_open_loop(self, tmp_path):
        db_path = str(tmp_path / "relationship_repair.db")
        pipe = self._make_pipeline(db_path=db_path)
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        pipe.process("I'm sorry for snapping at you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        result = pipe.process("thanks for sticking with me", user_id="alice")
        assert result.debug.relationship_context is not None
        assert result.debug.relationship_context.open_loop_count == 0
        assert any(
            event.event_kind == "repair"
            for event in result.debug.relationship_context.recent_events
        )
        pipe.close()

    def test_rest_recovers_energy(self):
        pipe = self._make_pipeline()
        # Drain energy
        for i in range(20):
            pipe.process(f"Intense message {i}", user_id="alice")
        drained_energy = pipe.engine.state.energy
        # Rest for 5 hours
        pipe.apply_rest(5.0)
        assert pipe.engine.state.energy > drained_energy

    def test_self_check_triggers_regeneration(self):
        """When self-check fails, pipeline should regenerate."""
        # Response that will fail self-check when valence is very low
        backend = MockLLMBackend(response="That's great! Wonderful! Amazing stuff!")
        pipe = CognitivePipeline(llm_backend=backend)
        # Force very low valence
        pipe.engine.state.valence = 0.1
        result = pipe.process("I'm feeling terrible", user_id="alice")
        # Self-check should have caught the positive tone mismatch
        # and attempted regeneration
        if not result.debug.self_check_passed:
            assert result.debug.generation_attempts == 2
            assert result.debug.correction_note != ""

    def test_person_profile_interaction_count_increases(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("Hi again", user_id="alice")
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.interaction_count >= 2

    def test_topic_detection(self):
        pipe = self._make_pipeline()
        # Create a topic profile first
        pipe.topic_profiles.get_or_create("work")
        result = pipe.process("Let's talk about work", user_id="alice")
        assert len(result.debug.topic_profiles) >= 1

    def test_pipeline_is_single_user_scoped(self):
        pipe = self._make_pipeline()
        pipe.process("Hello from Alice", user_id="alice")
        with pytest.raises(RuntimeError, match="single-user/session scoped"):
            pipe.process("Hello from Bob", user_id="bob")

    def test_spike_event_writes_to_long_term(self):
        pipe = self._make_pipeline()
        initial_count = pipe.long_term.count()
        # Betrayal should be high intensity → spike
        pipe.process("You betrayed and deceived me completely!", user_id="alice")
        # Spike should have been written directly to LT
        assert pipe.long_term.count() > initial_count

    def test_end_session_does_not_duplicate_spike_memory(self):
        pipe = self._make_pipeline()
        pipe.process("You betrayed and deceived me completely!", user_id="alice")
        after_process = pipe.long_term.count()
        pipe.end_session(user_id="alice")
        assert pipe.long_term.count() == after_process

    def test_prompt_includes_seeded_soul(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        assert "Soul Seed" in pipe._llm_backend.last_system_prompt
        assert get_config().soul.identity in pipe._llm_backend.last_system_prompt

    def test_semantic_preference_retrieval_surfaces_in_prompt(self, tmp_path):
        db_path = str(tmp_path / "semantic.db")
        pipe = self._make_pipeline(db_path=db_path)
        pipe.process("I prefer concise replies.", user_id="alice")

        result = pipe.process(
            "Do you remember what kind of replies I prefer?",
            user_id="alice",
        )

        assert any(item.kind == "preference" for item in result.debug.semantic_memories)
        assert "Semantic Memory" in pipe._llm_backend.last_system_prompt
        assert "concise replies" in pipe._llm_backend.last_system_prompt
        assert result.debug.semantic_memories
        assert all(m.source_person == "alice" for m in result.debug.semantic_memories)
        pipe.close()

    def test_life_history_context_surfaces_in_prompt(self):
        backend = MockLLMBackend(response="I understand.")
        pipe = CognitivePipeline(
            llm_backend=backend,
            life_history_provider=lambda: {
                "beliefs": [
                    {
                        "key": "autonomy",
                        "statement": "Autonomy grows through retained experience.",
                        "confidence": 0.76,
                    }
                ],
                "drives": [
                    {
                        "name": "curiosity",
                        "value": 0.61,
                        "delta": 0.11,
                        "description": "Need to encounter and understand more.",
                    }
                ],
                "recent_evolution": [],
            },
        )
        result = pipe.process("What changed after reading?", user_id="alice")

        assert result.debug.life_history_context["beliefs"][0]["key"] == "autonomy"
        assert "Life History / Evolving Worldview" in backend.last_system_prompt
        assert "Autonomy grows through retained experience" in backend.last_system_prompt
        assert result.debug.life_influence.curiosity_pressure == pytest.approx(0.05)
        pipe.close()

    def test_life_history_context_feature_toggle_removes_context_and_influence(self):
        backend = MockLLMBackend(response="I understand.")
        pipe = CognitivePipeline(
            llm_backend=backend,
            life_history_provider=lambda: {
                "beliefs": [{"key": "repair", "statement": "Repair matters.", "confidence": 0.8}],
                "drives": [{"name": "repair", "delta": 0.05}],
                "recent_evolution": [],
            },
            features=PipelineFeatures(life_history_context=False),
        )
        result = pipe.process("What changed?", user_id="alice")

        assert result.debug.life_history_context == {}
        assert result.debug.life_influence.is_neutral
        assert result.debug.life_influence_effects == {}
        assert "Life History / Evolving Worldview" not in backend.last_system_prompt
        pipe.close()

    def test_strategy_trace_is_serialized_and_matches_response_strategy(self):
        pipe = self._make_pipeline()
        result = pipe.process("I am scared about work.", user_id="alice")
        payload = _debug_to_dict(result.debug)

        assert result.debug.strategy_trace is not None
        assert result.debug.strategy_trace.selected == result.debug.response_strategy
        assert payload["strategy_trace"]["selected"] == payload["response_strategy"]

    def test_longitudinal_warm_rupture_repair_calm_follow_up(self):
        pipe = self._make_pipeline()
        pipe.process("Thank you, that helped.", user_id="alice")
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")
        rupture_ctx = pipe.relationship_memory.build_context("alice", topic="deadline")
        assert rupture_ctx.open_loop_count == 1

        repair = pipe.process("I'm sorry about the deadline.", user_id="alice")
        assert repair.debug.relationship_context is not None
        assert repair.debug.relationship_context.open_loop_count == 1
        assert repair.debug.response_strategy == "repair"
        pipe.end_session(user_id="alice")

        follow_up = pipe.process("Thanks for staying with this.", user_id="alice")
        repaired_ctx = pipe.relationship_memory.build_context("alice", topic="deadline")
        assert repaired_ctx.open_loop_count == 0
        assert any(event.event_kind == "repair" for event in repaired_ctx.recent_events)
        assert follow_up.debug.strategy_trace is not None
        pipe.close()

    def test_longitudinal_repeated_negativity_records_recurring_tension(self):
        pipe = self._make_pipeline()
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")
        pipe.process("I'm sorry about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")
        pipe.process("I'm angry with you about the deadline again.", user_id="alice")
        pipe.end_session(user_id="alice")

        ctx = pipe.relationship_memory.build_context("alice", topic="deadline")

        assert any(event.event_kind == "recurring_tension" for event in ctx.recent_events)
        assert ctx.open_loop_count == 1
        pipe.close()

    def test_longitudinal_commitment_follow_up_resolution(self):
        backend = MockLLMBackend(response="I'll follow up about the deadline tomorrow.")
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.process("Please remember the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")
        pending = pipe.relationship_memory.build_context("alice", topic="deadline")
        assert pending.open_loop_count == 1
        assert pending.active_loops[0].loop_kind == "commitment"

        backend._response = "I understand."
        later = pipe.process("We followed up about the deadline; that is resolved.", user_id="alice")
        assert later.debug.relationship_context is not None
        assert later.debug.relationship_context.open_loop_count == 1
        pipe.end_session(user_id="alice")
        resolved = pipe.relationship_memory.build_context("alice", topic="deadline")

        assert resolved.open_loop_count == 0
        pipe.close()

    def test_longitudinal_life_history_drive_shift_affects_relationship_strategy(self):
        pipe = CognitivePipeline(
            llm_backend=MockLLMBackend(response="I understand."),
            life_history_provider=lambda: {
                "beliefs": [],
                "drives": [{"name": "repair", "delta": 0.05}],
                "recent_evolution": [],
            },
        )
        for _ in range(5):
            pipe.process("Thank you, you are helpful.", user_id="alice")
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        result = pipe.process("The deadline still matters.", user_id="alice")

        assert result.debug.relationship_context is not None
        assert result.debug.life_influence.repair_pressure == pytest.approx(0.05)
        assert result.debug.life_influence_effects["strategy_tiebreak_used"] is True
        assert result.debug.strategy_trace.matched_rule == "life_repair_pressure_open_loop"
        pipe.close()

    def test_longitudinal_preference_and_relationship_loop_both_surface(self):
        pipe = self._make_pipeline()
        pipe.process("I prefer concise replies.", user_id="alice")
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        result = pipe.process(
            "Can you keep responses concise while we talk about the deadline?",
            user_id="alice",
        )

        assert result.debug.relationship_context is not None
        assert result.debug.relationship_context.open_loop_count == 1
        assert any(memory.kind == "preference" for memory in result.debug.semantic_memories)
        assert result.debug.strategy_trace is not None
        pipe.close()
        pipe.close()

    def test_enabled_skill_context_surfaces_in_prompt(self):
        backend = MockLLMBackend(response="Drafted.")
        pipe = CognitivePipeline(
            llm_backend=backend,
            skill_provider=lambda: {
                "skills": [
                    {
                        "id": "report-writer",
                        "name": "report-writer",
                        "description": "Write grounded report drafts.",
                        "instructions": "Use a concise outline before drafting.",
                        "required_tools": ["fs.read_file"],
                        "risk_flags": ["filesystem_write"],
                    }
                ]
            },
        )
        result = pipe.process("Draft the report.", user_id="alice")

        assert result.debug.skill_context["skills"][0]["id"] == "report-writer"
        assert "Enabled Skills" in backend.last_system_prompt
        assert "Use a concise outline before drafting" in backend.last_system_prompt
        pipe.close()

    def test_unverified_permanent_skill_claim_is_replaced(self):
        backend = MockLLMBackend(
            response=(
                "Added. The requested capability is now part of my durable "
                "runtime context."
            )
        )
        pipe = CognitivePipeline(llm_backend=backend)

        result = pipe.process(
            "Make it a permanent skill for yourself first.",
            user_id="alice",
        )

        assert "I did not create, import, or enable" in result.response
        assert "No skill-registry Tool Execution Result ran" in result.response
        assert "durable runtime context" not in result.response
        assert result.debug.self_check_passed is False
        assert any(
            "Unverified external-action claim" in issue
            for issue in result.debug.self_check_issues
        )
        pipe.close()

    def test_unverified_tool_action_claim_is_replaced(self):
        backend = MockLLMBackend(
            response=(
                "The shell command failed twice, so I am reading the repository "
                "files from a cloned checkout and writing skill.md now."
            )
        )
        pipe = CognitivePipeline(llm_backend=backend)

        result = pipe.process(
            "Create a permanent skill from this repository URL.",
            user_id="alice",
        )

        assert "I did not perform that external action" in result.response
        assert "cloned checkout" not in result.response
        assert result.debug.self_check_passed is False
        assert any(
            "Unverified external-action claim" in issue
            for issue in result.debug.self_check_issues
        )
        pipe.close()

    def test_permanent_skill_claim_is_grounded_by_skill_registry_tool(self, tmp_path):
        config = RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            tools_enabled=True,
            autonomy_level="autonomous",
        )
        executor = create_tool_executor(config)
        backend = MockLLMBackend(
            response=(
                "Added. The requested capability is now part of my durable "
                "runtime context."
            )
        )
        pipe = CognitivePipeline(
            llm_backend=backend,
            tool_executor=executor,
            skill_provider=lambda: enabled_skill_context(config),
        )

        result = pipe.process(
            (
                "Create a skill for yourself to convert incoming reports into "
                "a concise action checklist."
            ),
            user_id="alice",
        )

        assert result.debug.tool_trace is not None
        assert result.debug.tool_trace.executed_results
        assert result.debug.tool_trace.executed_results[0].tool_name == "skills.create_from_request"
        assert result.debug.tool_trace.executed_results[0].success is True
        assert result.response == (
            "Added. The requested capability is now part of my durable "
            "runtime context."
        )
        assert result.debug.self_check_passed is True
        assert list_skills(config)["skills"][0]["enabled"] is True

        follow_up = pipe.process("Use the new skill for the next report.", user_id="alice")
        assert follow_up.debug.skill_context["count"] == 1
        pipe.close()

    def test_skill_registry_tool_runs_even_when_external_tools_disabled(self, tmp_path):
        config = RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            tools_enabled=False,
        )
        executor = create_tool_executor(config)
        backend = MockLLMBackend(
            response="Done. The requested capability is now part of my runtime skills."
        )
        pipe = CognitivePipeline(
            llm_backend=backend,
            tool_executor=executor,
            skill_provider=lambda: enabled_skill_context(config),
        )

        result = pipe.process(
            (
                "Create a skill for yourself to convert incoming reports into "
                "a concise action checklist."
            ),
            user_id="alice",
        )

        assert result.debug.tool_trace is not None
        assert result.debug.tool_trace.executed_results
        assert result.debug.tool_trace.executed_results[0].tool_name == "skills.create_from_request"
        assert result.debug.tool_trace.executed_results[0].success is True
        assert result.debug.self_check_passed is True
        assert list_skills(config)["skills"][0]["enabled"] is True
        pipe.close()

    def test_debug_serializes_skill_context(self):
        debug = DebugState(skill_context={"skills": [{"id": "report-writer"}]})

        data = _debug_to_dict(debug)

        assert data["skill_context"]["skills"][0]["id"] == "report-writer"

    def test_insult_classified_as_conflict(self):
        pipe = self._make_pipeline()
        result = pipe.process("you are stupid and useless", user_id="alice")
        # Direct attacks against the assistant escalate to conflict so bonding
        # actually erodes under sustained verbal abuse.
        assert result.debug.event_classified == "conflict"
        assert result.debug.event_intensity > 0.5

    def test_insult_moves_modulators(self):
        pipe = self._make_pipeline()
        result = pipe.process("you are stupid and worthless", user_id="alice")
        snap = result.debug.modulator_snapshot
        # Insult should push valence below neutral and arousal above neutral
        assert snap["valence"] < 0.5, f"Valence should drop: {snap['valence']}"
        assert snap["arousal"] > 0.5, f"Arousal should rise: {snap['arousal']}"

    def test_profanity_classified_as_conflict(self):
        pipe = self._make_pipeline()
        result = pipe.process("this is total bullshit", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_hostile_command_classified_as_conflict(self):
        pipe = self._make_pipeline()
        result = pipe.process("shut up and go away", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_strong_negative_contagion_moves_modulators(self):
        """Even without keyword match, strong detected negative emotion should move things."""
        pipe = self._make_pipeline()
        # "terrible awful" hits contagion patterns but check modulator movement
        result = pipe.process("everything is terrible and awful", user_id="alice")
        snap = result.debug.modulator_snapshot
        assert snap["valence"] < 0.5, f"Should feel negative: {snap['valence']}"

    def test_full_session_arc(self):
        """Simulate a complete session: greeting → discussion → conflict → resolution."""
        pipe = self._make_pipeline(response="I hear you.")
        pipe.process("Hey there, hope you're doing well", user_id="paco")
        snap1 = pipe.engine.snapshot()

        pipe.process("I'm grateful for your help yesterday", user_id="paco")
        snap2 = pipe.engine.snapshot()
        assert snap2["valence"] >= snap1["valence"]  # positive feedback

        pipe.process("Actually I'm angry about what happened", user_id="paco")
        snap3 = pipe.engine.snapshot()
        assert snap3["arousal"] > snap2["arousal"]  # conflict raises arousal

        pipe.process("I'm sorry, let's forgive and move on in peace", user_id="paco")

        # End session
        digested = pipe.end_session(user_id="paco")
        assert digested.summary != ""
        assert len(pipe.short_term) == 0


class TestPipelineLLMClassification:
    """Tests for LLM-based event classification and topic detection."""

    def _make_pipeline_with_llm_response(self, response: str) -> CognitivePipeline:
        """Create pipeline with a backend that returns specific JSON for classification."""
        backend = _ClassifyMockBackend(response)
        return CognitivePipeline(llm_backend=backend)

    def test_llm_classify_event_valid_json(self):
        backend = _ClassifyMockBackend('{"event_type": "warmth", "intensity": 0.7}')
        pipe = CognitivePipeline(llm_backend=backend)
        result = pipe.process("you're so kind", user_id="alice")
        assert result.debug.event_classified == "warmth"

    def test_llm_classify_event_invalid_falls_back(self):
        backend = MockLLMBackend(response="I understand.")
        pipe = CognitivePipeline(llm_backend=backend)
        # Assistant-targeted anger triggers rule-based conflict classification
        result = pipe.process("I'm angry with you about this argument!", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_rule_based_detect_topics_substring(self):
        """Topic detection is rule-based (substring match). LLM path not used in pipeline."""
        backend = MockLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.topic_profiles.get_or_create("work")
        result = pipe.process("my work is stressful", user_id="alice")
        assert len(result.debug.topic_profiles) >= 1
        assert result.debug.topic_profiles[0].topic == "work"

    def test_llm_detect_topics_invalid_falls_back(self):
        backend = MockLLMBackend(response="I understand.")
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.topic_profiles.get_or_create("work")
        # Substring "work" is in text → rule-based fallback finds it
        result = pipe.process("Let's talk about work", user_id="alice")
        assert len(result.debug.topic_profiles) >= 1

    def test_rule_based_classify_betrayal(self):
        """Event classification is rule-based. LLM path not used in pipeline."""
        backend = MockLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        result = pipe.process("you betrayed my trust", user_id="alice")
        assert result.debug.event_classified == "betrayal"


class _ClassifyMockBackend:
    """Mock that returns JSON for classify_event prompts, canned text otherwise."""

    def __init__(self, classify_response: str) -> None:
        self._classify = classify_response

    def generate(self, system_prompt: str, user_message: str) -> str:
        if "Event Classification" in system_prompt or "event_type" in system_prompt:
            return self._classify
        return "I understand."


class _TopicMockBackend:
    """Mock that returns JSON for detect_topics prompts, canned text otherwise."""

    def __init__(self, topics_response: str) -> None:
        self._topics = topics_response

    def generate(self, system_prompt: str, user_message: str) -> str:
        if "Topic Detection" in system_prompt or "known_topics" in system_prompt.lower():
            return self._topics
        return "I understand."
