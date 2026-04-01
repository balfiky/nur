"""Scripted calibration scenarios.

Each scenario is a sequence of (message, user_id) pairs that exercise
specific emotional dynamics. Run through the full CognitivePipeline
and collect modulator traces at each step.

Scenarios:
1. Trust building (10 sessions of warm interaction)
2. Betrayal (trust build → sudden betrayal)
3. Topic avoidance (repeated negative associations)
4. Contagion (user emotion mirrors into AI state)
5. Conflict recovery (fight → resolution → recovery)
6. Energy depletion (long session drains energy)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pipeline import CognitivePipeline, PipelineResponse, DebugState
from core.dual_process.generator import MockLLMBackend
from core.memory.digestion import DigestedSession


# ---------------------------------------------------------------------------
# Trace data structures
# ---------------------------------------------------------------------------

@dataclass
class TracePoint:
    """A single point in a modulator trace."""
    step: int
    message: str
    modulators: dict[str, float]
    event_type: str
    event_intensity: float
    is_spike: bool
    emotion_label: str
    energy: float


@dataclass
class SessionTrace:
    """Trace of a single session."""
    session_id: int
    points: list[TracePoint] = field(default_factory=list)
    digestion: DigestedSession | None = None


@dataclass
class ScenarioResult:
    """Full result of running a scenario."""
    name: str
    sessions: list[SessionTrace] = field(default_factory=list)

    @property
    def all_points(self) -> list[TracePoint]:
        """All trace points across all sessions, in order."""
        points = []
        for session in self.sessions:
            points.extend(session.points)
        return points

    def modulators_over_time(self, modulator: str) -> list[float]:
        """Extract a single modulator's values over the full scenario."""
        return [p.modulators.get(modulator, 0.0) for p in self.all_points]

    def energy_over_time(self) -> list[float]:
        """Extract energy values over the full scenario."""
        return [p.energy for p in self.all_points]


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def _run_session(
    pipe: CognitivePipeline,
    messages: list[str],
    user_id: str,
    session_id: int,
    step_offset: int = 0,
) -> SessionTrace:
    """Run a single session and collect trace points."""
    trace = SessionTrace(session_id=session_id)

    for i, msg in enumerate(messages):
        resp = pipe.process(msg, user_id=user_id)
        debug = resp.debug
        point = TracePoint(
            step=step_offset + i,
            message=msg,
            modulators=dict(debug.modulator_snapshot),
            event_type=debug.event_classified,
            event_intensity=debug.event_intensity,
            is_spike=debug.is_spike,
            emotion_label=debug.emotion_label,
            energy=debug.energy_after,
        )
        trace.points.append(point)

    # End session
    digested = pipe.end_session(user_id)
    trace.digestion = digested
    return trace


# ---------------------------------------------------------------------------
# Scenario 1: Trust Building (10 sessions)
# ---------------------------------------------------------------------------

def scenario_trust_building() -> ScenarioResult:
    """10 sessions of warm, positive interaction.

    Expected: trust climbs steadily, valence stays positive,
    bonding increases, energy recovers between sessions.
    """
    backend = MockLLMBackend(response="I'm glad to help!")
    pipe = CognitivePipeline(llm_backend=backend)
    result = ScenarioResult(name="trust_building")
    step = 0

    for session_num in range(10):
        messages = [
            "Hello! How are you today?",
            "Thank you for helping me!",
            "You're doing a great job, I appreciate it.",
            "That's really kind of you.",
            "Thanks again, see you next time!",
        ]
        trace = _run_session(pipe, messages, "alice", session_num, step)
        result.sessions.append(trace)
        step += len(messages)

        # Rest between sessions (2 hours)
        pipe.apply_rest(2.0)

    return result


# ---------------------------------------------------------------------------
# Scenario 2: Betrayal
# ---------------------------------------------------------------------------

def scenario_betrayal() -> ScenarioResult:
    """Build trust over 3 sessions, then betray.

    Expected: trust builds, then crashes. Valence spikes negative.
    Arousal spikes. Recovery is slow.
    """
    backend = MockLLMBackend(response="I understand.")
    pipe = CognitivePipeline(llm_backend=backend)
    result = ScenarioResult(name="betrayal")
    step = 0

    # 3 trust-building sessions
    for session_num in range(3):
        messages = [
            "Hey, thanks for being here.",
            "I really appreciate your help!",
            "You're so kind, thank you.",
        ]
        trace = _run_session(pipe, messages, "bob", session_num, step)
        result.sessions.append(trace)
        step += len(messages)
        pipe.apply_rest(1.0)

    # Betrayal session
    messages = [
        "I trusted you and you lied to me.",
        "You betrayed my trust completely.",
        "I feel so deceived and angry.",
    ]
    trace = _run_session(pipe, messages, "bob", 3, step)
    result.sessions.append(trace)
    step += len(messages)
    pipe.apply_rest(2.0)

    # Recovery attempt
    messages = [
        "I'm trying to move past this.",
        "Can we start over?",
        "I want to forgive you.",
    ]
    trace = _run_session(pipe, messages, "bob", 4, step)
    result.sessions.append(trace)

    return result


# ---------------------------------------------------------------------------
# Scenario 3: Topic Avoidance
# ---------------------------------------------------------------------------

def scenario_topic_avoidance() -> ScenarioResult:
    """Repeated negative associations with a topic until avoidance triggers.

    Expected: topic emotional charge rises, eventually flagged for avoidance.
    """
    backend = MockLLMBackend(response="Let me think about that.")
    pipe = CognitivePipeline(llm_backend=backend)
    result = ScenarioResult(name="topic_avoidance")
    step = 0

    # Create the topic first
    pipe.topic_profiles.get_or_create("politics")

    # Negative associations
    for session_num in range(5):
        messages = [
            "Let's talk about politics.",
            "Politics makes me so angry and frustrated!",
            "I hate how divisive politics has become.",
            "Every political discussion turns into a fight.",
        ]
        trace = _run_session(pipe, messages, "carol", session_num, step)
        result.sessions.append(trace)
        step += len(messages)

        # Record negative topic associations
        pipe.topic_profiles.record_negative("politics", intensity=0.8)
        pipe.topic_profiles.record_conflict("politics")

        pipe.apply_rest(1.0)

    return result


# ---------------------------------------------------------------------------
# Scenario 4: Contagion
# ---------------------------------------------------------------------------

def scenario_contagion() -> ScenarioResult:
    """User's strong emotions should partially mirror into AI state.

    Expected: high-arousal user messages raise AI arousal.
    Negative user valence drags AI valence down.
    """
    backend = MockLLMBackend(response="I can feel that.")
    pipe = CognitivePipeline(llm_backend=backend)
    result = ScenarioResult(name="contagion")

    # Single session with escalating emotion
    messages = [
        "Hi there, I'm doing okay.",                     # neutral
        "Actually, I'm feeling a bit anxious today.",     # mild negative
        "I'm really stressed about this deadline.",       # moderate negative
        "I'M SO FRUSTRATED I COULD SCREAM!!!",           # extreme negative
        "Okay... I'm calming down now.",                  # de-escalation
        "Thank you for listening. I feel a bit better.",  # recovery
    ]
    trace = _run_session(pipe, messages, "dave", 0, 0)
    result.sessions.append(trace)

    return result


# ---------------------------------------------------------------------------
# Scenario 5: Conflict Recovery
# ---------------------------------------------------------------------------

def scenario_conflict_recovery() -> ScenarioResult:
    """Fight → resolution → recovery arc.

    Expected: valence drops during conflict, arousal spikes,
    then both recover during resolution. Trust takes a hit
    but partially recovers.
    """
    backend = MockLLMBackend(response="Let me help with that.")
    pipe = CognitivePipeline(llm_backend=backend)
    result = ScenarioResult(name="conflict_recovery")

    # Pre-conflict warmth
    messages = ["Hello! Great to see you.", "This is really helpful, thanks!"]
    trace = _run_session(pipe, messages, "eve", 0, 0)
    result.sessions.append(trace)
    pipe.apply_rest(0.5)

    # Conflict
    messages = [
        "That answer was completely wrong!",
        "I'm angry that you gave me bad advice.",
        "This argument is going nowhere.",
    ]
    trace = _run_session(pipe, messages, "eve", 1, 2)
    result.sessions.append(trace)
    pipe.apply_rest(0.5)

    # Resolution
    messages = [
        "I'm sorry I got so upset.",
        "Let's try to resolve this peacefully.",
        "I forgive the mistake. We all make errors.",
        "Thank you for being patient with me.",
    ]
    trace = _run_session(pipe, messages, "eve", 2, 5)
    result.sessions.append(trace)

    return result


# ---------------------------------------------------------------------------
# Scenario 6: Energy Depletion
# ---------------------------------------------------------------------------

def scenario_energy_depletion() -> ScenarioResult:
    """Long session that depletes energy.

    Expected: energy drains steadily, emotion label shifts
    to exhausted/irritable at low energy.
    """
    backend = MockLLMBackend(response="Processing...")
    pipe = CognitivePipeline(llm_backend=backend)
    result = ScenarioResult(name="energy_depletion")

    # 40-message session
    messages = [f"Tell me about topic number {i+1}." for i in range(40)]
    trace = _run_session(pipe, messages, "frank", 0, 0)
    result.sessions.append(trace)
    pipe.apply_rest(1.0)

    # Short session after partial rest
    messages = [
        "Are you feeling better now?",
        "Let's do a few more questions.",
        "One more thing before we stop.",
    ]
    trace = _run_session(pipe, messages, "frank", 1, 40)
    result.sessions.append(trace)

    return result


# ---------------------------------------------------------------------------
# Run all scenarios
# ---------------------------------------------------------------------------

ALL_SCENARIOS = [
    ("trust_building", scenario_trust_building),
    ("betrayal", scenario_betrayal),
    ("topic_avoidance", scenario_topic_avoidance),
    ("contagion", scenario_contagion),
    ("conflict_recovery", scenario_conflict_recovery),
    ("energy_depletion", scenario_energy_depletion),
]


def run_all_scenarios() -> dict[str, ScenarioResult]:
    """Run all calibration scenarios and return results."""
    results = {}
    for name, func in ALL_SCENARIOS:
        results[name] = func()
    return results
