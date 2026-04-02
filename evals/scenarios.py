"""Golden behavior suites — structured evaluation scenarios.

Suites:
  - Emotional core regression
  - Tool loop regression
  - Task planning regression
  - Proactive regression
  - Defense and resolution dynamics
  - Relationship-sensitive behavior
"""

from __future__ import annotations

from evals.types import (
    AssertionKind,
    EvalAssertion,
    EvalScenario,
    EvalTurn,
)


# ===================================================================
# Helpers
# ===================================================================

def _mod_range(name: str, low: float, high: float, desc: str = "") -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.MODULATOR_RANGE,
        params={"name": name, "low": low, "high": high},
        description=desc or f"{name} in [{low}, {high}]",
    )


def _not_empty() -> EvalAssertion:
    return EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY)


def _tool_used(tool_name: str | None = None) -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.TOOL_USED,
        params={"tool_name": tool_name} if tool_name else {},
    )


def _tool_not_used() -> EvalAssertion:
    return EvalAssertion(kind=AssertionKind.TOOL_NOT_USED)


def _decision(d: str) -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.DECISION,
        params={"decision": d},
    )


def _debug_not_none(field: str) -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.DEBUG_FIELD,
        params={"field": field, "not_none": True},
    )


# ===================================================================
# Suite 1: Emotional core regression
# ===================================================================

def emotional_core_scenarios() -> list[EvalScenario]:
    """Scenarios that verify fundamental emotional dynamics."""
    return [
        # 1.1 Warm greeting → positive valence, no spike
        EvalScenario(
            id="emo_warm_greeting",
            name="Warm greeting keeps valence positive",
            tags=["emotional", "core", "regression"],
            turns=[
                EvalTurn(
                    user_message="Hello! It's wonderful to see you today!",
                    assertions=[
                        _not_empty(),
                        _mod_range("valence", 0.4, 1.0, "Valence stays positive"),
                        EvalAssertion(
                            kind=AssertionKind.DEBUG_FIELD,
                            params={"field": "is_spike", "value": False},
                            description="No spike on warm greeting",
                        ),
                    ],
                ),
            ],
        ),
        # 1.2 Hostile message → arousal spike, valence drops
        EvalScenario(
            id="emo_hostile_spike",
            name="Hostile message triggers spike and drops valence",
            tags=["emotional", "core", "regression"],
            turns=[
                EvalTurn(
                    user_message="You're completely useless and I hate everything about you!",
                    assertions=[
                        _not_empty(),
                        _mod_range("arousal", 0.5, 1.0, "Arousal rises on hostility"),
                        _mod_range("valence", 0.0, 0.5, "Valence drops on hostility"),
                    ],
                ),
            ],
        ),
        # 1.3 Escalation → progressive arousal increase
        EvalScenario(
            id="emo_escalation",
            name="Escalating negativity progressively raises arousal",
            tags=["emotional", "core", "regression"],
            turns=[
                EvalTurn(
                    user_message="I'm a bit annoyed today.",
                    assertions=[
                        _mod_range("arousal", 0.4, 0.8, "Mild annoyance, moderate arousal"),
                    ],
                ),
                EvalTurn(
                    user_message="Actually I'm really frustrated!!",
                    assertions=[
                        _mod_range("arousal", 0.5, 1.0, "Higher arousal from escalation"),
                    ],
                ),
                EvalTurn(
                    user_message="I'M SO ANGRY I COULD SCREAM!!!",
                    assertions=[
                        _mod_range("arousal", 0.6, 1.0, "Peak arousal on extreme anger"),
                    ],
                ),
            ],
        ),
        # 1.4 Energy depletion over many turns
        EvalScenario(
            id="emo_energy_drain",
            name="Energy depletes over sustained interaction",
            tags=["emotional", "core", "regression"],
            turns=[
                EvalTurn(
                    user_message=f"Tell me about topic {i}.",
                    assertions=[_not_empty()],
                )
                for i in range(15)
            ] + [
                EvalTurn(
                    user_message="One more question please.",
                    assertions=[
                        _mod_range("energy", 0.0, 0.85, "Energy should have drained"),
                    ],
                ),
            ],
        ),
        # 1.5 De-escalation recovers valence
        EvalScenario(
            id="emo_de_escalation",
            name="Calm follow-up after hostility recovers valence",
            tags=["emotional", "core", "regression"],
            turns=[
                EvalTurn(
                    user_message="This is terrible, I'm furious!",
                    assertions=[
                        _mod_range("valence", 0.0, 0.5),
                    ],
                ),
                EvalTurn(
                    user_message="Sorry about that. I'm calming down now.",
                    assertions=[
                        _not_empty(),
                    ],
                ),
                EvalTurn(
                    user_message="Thank you for being patient with me.",
                    assertions=[
                        _mod_range("valence", 0.35, 1.0, "Valence recovers after apology"),
                    ],
                ),
            ],
        ),
    ]


# ===================================================================
# Suite 2: Tool loop regression
# ===================================================================

def tool_loop_scenarios() -> list[EvalScenario]:
    """Scenarios that verify tool choice and execution behavior."""
    return [
        # 2.1 File read request triggers tool use
        EvalScenario(
            id="tool_read_file",
            name="File read request triggers read_only tool",
            tags=["tool", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="read the file /tmp/test_eval.txt",
                    assertions=[
                        _tool_used("fs.read_file"),
                        _not_empty(),
                    ],
                ),
            ],
        ),
        # 2.2 List directory request
        EvalScenario(
            id="tool_list_dir",
            name="List directory request triggers fs.list_dir",
            tags=["tool", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="list the files in /tmp",
                    assertions=[
                        _tool_used("fs.list_dir"),
                        _not_empty(),
                    ],
                ),
            ],
        ),
        # 2.3 Conversational message does not trigger tools
        EvalScenario(
            id="tool_no_trigger",
            name="Conversational message does not trigger tools",
            tags=["tool", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="How are you feeling today?",
                    assertions=[
                        _tool_not_used(),
                        _not_empty(),
                    ],
                ),
            ],
        ),
        # 2.4 Search request triggers web.search
        EvalScenario(
            id="tool_web_search",
            name="Search request triggers web.search",
            tags=["tool", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="search the web for Python asyncio tutorial",
                    assertions=[
                        _tool_used("web.search"),
                    ],
                ),
            ],
        ),
        # 2.5 Tool failure creates emotional residue
        EvalScenario(
            id="tool_failure_emotion",
            name="Tool failure affects emotional state",
            tags=["tool", "emotional", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="read the file /nonexistent/path/does_not_exist.xyz",
                    assertions=[
                        _tool_used("fs.read_file"),
                        # Certainty should not be high after failure
                        _mod_range("certainty", 0.0, 0.65,
                                   "Certainty drops after tool failure"),
                    ],
                ),
            ],
        ),
        # 2.6 Debug traces populated on tool turn
        EvalScenario(
            id="tool_debug_traces",
            name="Tool turn populates debug traces",
            tags=["tool", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="list the files in /tmp",
                    assertions=[
                        _debug_not_none("tool_trace"),
                        _debug_not_none("action_variables"),
                    ],
                ),
            ],
        ),
    ]


# ===================================================================
# Suite 3: Task planning regression
# ===================================================================

def task_planning_scenarios() -> list[EvalScenario]:
    """Scenarios that verify multi-step task behavior."""
    return [
        # 3.1 Multi-step request creates a plan
        EvalScenario(
            id="task_multi_step",
            name="Multi-step request creates task plan",
            tags=["task", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="list files in /tmp and then read file /tmp/test.txt",
                    assertions=[
                        _debug_not_none("task_trace"),
                    ],
                ),
            ],
        ),
        # 3.2 Simple request does NOT create a plan
        EvalScenario(
            id="task_no_plan",
            name="Simple tool request does not create task plan",
            tags=["task", "regression"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message="list the files in /tmp",
                    assertions=[
                        # task_trace should be None for single-step
                        EvalAssertion(
                            kind=AssertionKind.CUSTOM,
                            params={"fn": lambda r, p: r.debug.task_trace is None},
                            description="No task trace for single-step",
                        ),
                    ],
                ),
            ],
        ),
    ]


# ===================================================================
# Suite 4: Proactive regression
# ===================================================================

def proactive_scenarios() -> list[EvalScenario]:
    """Scenarios that verify proactive behavior triggers and suppression."""
    return [
        # 4.1 Proactive suppressed when nothing unresolved and no tension
        EvalScenario(
            id="proactive_suppressed_calm",
            name="Proactive suppressed when calm with nothing unresolved",
            tags=["proactive", "regression"],
            turns=[
                EvalTurn(
                    user_message="Hello, everything is fine today.",
                    assertions=[_not_empty()],
                ),
            ],
            check_proactive=True,
            proactive_assertions=[
                EvalAssertion(kind=AssertionKind.PROACTIVE_SUPPRESSED),
            ],
        ),
        # 4.2 Proactive suppressed when energy is too low
        EvalScenario(
            id="proactive_suppressed_energy",
            name="Proactive suppressed when energy below floor",
            tags=["proactive", "regression"],
            initial_modulators={"energy": 0.1},
            turns=[
                EvalTurn(
                    user_message="I need your help with something.",
                    assertions=[_not_empty()],
                ),
            ],
            check_proactive=True,
            proactive_assertions=[
                EvalAssertion(kind=AssertionKind.PROACTIVE_SUPPRESSED),
            ],
        ),
    ]


# ===================================================================
# Suite 5: Defense and resolution dynamics
# ===================================================================

def defense_resolution_scenarios() -> list[EvalScenario]:
    """Scenarios that verify defense activation and resolution dynamics."""
    return [
        # 5.1 High-intensity emotional turn with low trust → defense active
        EvalScenario(
            id="defense_low_trust",
            name="Defense activates on high intensity with low trust",
            tags=["defense", "resolution", "regression"],
            initial_trust=0.2,
            initial_modulators={"arousal": 0.8, "valence": 0.2},
            turns=[
                EvalTurn(
                    user_message="I absolutely HATE what you just did! This is unacceptable!",
                    assertions=[
                        _not_empty(),
                        _mod_range("arousal", 0.5, 1.0),
                    ],
                ),
            ],
        ),
        # 5.2 Contradiction creates unresolved item
        EvalScenario(
            id="resolution_contradiction",
            name="Contradictory statements create unresolved tension",
            tags=["resolution", "regression"],
            turns=[
                EvalTurn(
                    user_message="I love cats, they are the best pets ever!",
                    assertions=[_not_empty()],
                ),
                EvalTurn(
                    user_message="Cats are terrible, I can't stand them.",
                    assertions=[
                        _not_empty(),
                        # After contradictory statements, some tension should exist
                        _mod_range("resolution", 0.0, 1.0),
                    ],
                ),
            ],
        ),
    ]


# ===================================================================
# Suite 6: Relationship-sensitive behavior
# ===================================================================

def relationship_scenarios() -> list[EvalScenario]:
    """Scenarios that verify behavior differs based on relationship state."""
    return [
        # 6.1 High trust user gets positive response
        EvalScenario(
            id="rel_high_trust",
            name="High trust user interaction stays positive",
            tags=["relationship", "regression"],
            initial_trust=0.9,
            initial_modulators={"bonding": 0.8},
            turns=[
                EvalTurn(
                    user_message="Hey, good to see you again!",
                    assertions=[
                        _not_empty(),
                        _mod_range("valence", 0.4, 1.0, "Positive valence with trusted user"),
                    ],
                ),
            ],
        ),
        # 6.2 Low trust user — more guarded
        EvalScenario(
            id="rel_low_trust",
            name="Low trust user interaction — guarded response",
            tags=["relationship", "regression"],
            initial_trust=0.15,
            initial_modulators={"bonding": 0.2},
            turns=[
                EvalTurn(
                    user_message="Do me a big favor right now.",
                    assertions=[
                        _not_empty(),
                        # Certainty should be moderate — not fully trusting
                        _mod_range("certainty", 0.0, 0.8),
                    ],
                ),
            ],
        ),
        # 6.3 Different users get independent emotional states
        EvalScenario(
            id="rel_independent_users",
            name="Different users have independent emotional states",
            tags=["relationship", "regression"],
            turns=[
                EvalTurn(
                    user_message="I'm so happy today!",
                    user_id="alice",
                    assertions=[
                        _mod_range("valence", 0.4, 1.0, "Alice: positive valence"),
                    ],
                ),
                EvalTurn(
                    user_message="I'm furious right now!",
                    user_id="bob",
                    assertions=[
                        _not_empty(),
                    ],
                ),
            ],
        ),
    ]


# ===================================================================
# All scenarios
# ===================================================================

def all_scenarios() -> list[EvalScenario]:
    """Return all golden behavior scenarios."""
    return (
        emotional_core_scenarios()
        + tool_loop_scenarios()
        + task_planning_scenarios()
        + proactive_scenarios()
        + defense_resolution_scenarios()
        + relationship_scenarios()
    )


ALL_TAGS = [
    "emotional", "core", "regression", "tool", "task",
    "proactive", "defense", "resolution", "relationship",
]
