"""Inner dialogue — iterative fast/slow path deliberation.

v2's core feature: 2-3 rounds where the intuitive voice generates,
the reflective voice evaluates, and they negotiate. Deadlocks produce
unresolved items that feed the resolution modulator.

No pure math here — this is where LLM calls live.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config.loader import get_config
from core.types import (
    DialogueRound,
    InnerDialogueTrace,
    LongTermEntry,
    ModulatorState,
    PersonProfile,
    SelfProfile,
    TopicProfile,
    UnresolvedItem,
    ValueHierarchy,
)

if TYPE_CHECKING:
    from core.dual_process.generator import LLMBackend


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ROUNDS = 3
AROUSAL_BYPASS_THRESHOLD = 0.8    # too activated to deliberate
ENERGY_BYPASS_THRESHOLD = 0.2     # too tired to deliberate
RESOLUTION_INSIST_THRESHOLD = 0.6  # slow path insists on unresolved items
CALM_AROUSAL_THRESHOLD = 0.55     # near-baseline arousal + low resolution = skip entirely
CALM_RESOLUTION_THRESHOLD = 0.3   # below this + near-baseline arousal = skip entirely

_PROMPT_MAP = {
    "fast_path.md": "fast_path_prompt",
    "slow_path.md": "slow_path_prompt",
    "fast_path_revision.md": "fast_path_revision_prompt",
    "arbiter.md": "arbiter_prompt",
}


def _load_template(name: str) -> str:
    """Load prompt template through config.loader (unified loading)."""
    attr = _PROMPT_MAP.get(name)
    if attr:
        return getattr(get_config(), attr, "")
    return ""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _format_modulator_snapshot(state: ModulatorState) -> str:
    d = state.to_dict()
    return ", ".join(f"{k}={v:.2f}" for k, v in d.items())


def _format_person_summary(person: PersonProfile | None) -> str:
    if not person:
        return "No person context"
    return (
        f"{person.name or person.person_id}: "
        f"trust={person.trust:.2f}, "
        f"interactions={person.interaction_count}, "
        f"stress_response={person.stress_response}"
    )


def _format_self_summary(self_profile: SelfProfile | None) -> str:
    if not self_profile:
        return "No self-profile"
    parts = []
    if self_profile.strengths:
        parts.append(f"Strengths: {', '.join(self_profile.strengths)}")
    if self_profile.flaws:
        parts.append(f"Flaws: {', '.join(self_profile.flaws)}")
    if self_profile.triggers:
        parts.append(f"Triggers: {', '.join(self_profile.triggers)}")
    if self_profile.dissonance > 0.2:
        parts.append(f"Dissonance: {self_profile.dissonance:.2f}")
    return "; ".join(parts) if parts else "No notable patterns"


def _format_values(values: ValueHierarchy | None) -> str:
    if not values:
        return "No values defined"
    return ", ".join(f"{k}={v:.2f}" for k, v in values.ranked())


def _format_unresolved(items: list[UnresolvedItem]) -> str:
    if not items:
        return "None"
    lines = []
    for item in items[:5]:
        lines.append(f"- [{item.source}] {item.description} (intensity={item.intensity:.2f})")
    return "\n".join(lines)


def _format_memories(memories: list[LongTermEntry]) -> str:
    if not memories:
        return "None"
    lines = []
    for mem in memories[:5]:
        tag = " [SPIKE]" if mem.spike else ""
        lines.append(f"- {mem.summary} (valence={mem.emotional_valence:.2f}){tag}")
    return "\n".join(lines)


def build_fast_path_prompt(
    state: ModulatorState,
    person: PersonProfile | None,
    contagion_summary: str,
    short_term_summary: str,
) -> str:
    template = _load_template("fast_path.md")
    if template:
        prompt = template
        prompt = prompt.replace("{modulator_snapshot}", _format_modulator_snapshot(state))
        prompt = prompt.replace("{person_profile_summary}", _format_person_summary(person))
        prompt = prompt.replace("{contagion_result}", contagion_summary)
        prompt = prompt.replace("{short_term_summary}", short_term_summary)
        return prompt
    # Fallback
    return (
        "You are the intuitive, gut-reaction voice. Respond based on feelings.\n"
        f"Emotional state: {_format_modulator_snapshot(state)}\n"
        f"Person: {_format_person_summary(person)}\n"
        "Generate your gut response."
    )


def build_slow_path_prompt(
    state: ModulatorState,
    person: PersonProfile | None,
    self_profile: SelfProfile | None,
    values: ValueHierarchy | None,
    unresolved: list[UnresolvedItem],
    memories: list[LongTermEntry],
    fast_candidate: str,
) -> str:
    template = _load_template("slow_path.md")
    if template:
        prompt = template
        prompt = prompt.replace("{modulator_snapshot}", _format_modulator_snapshot(state))
        prompt = prompt.replace("{person_profile_summary}", _format_person_summary(person))
        prompt = prompt.replace("{self_profile_summary}", _format_self_summary(self_profile))
        prompt = prompt.replace("{values}", _format_values(values))
        prompt = prompt.replace("{resolution_items}", _format_unresolved(unresolved))
        prompt = prompt.replace("{ltm_retrieval}", _format_memories(memories))
        prompt = prompt.replace("{fast_path_candidate}", fast_candidate)
        return prompt
    # Fallback
    return (
        "You are the reflective voice. Evaluate this candidate response.\n"
        f"Self-profile: {_format_self_summary(self_profile)}\n"
        f"Values: {_format_values(values)}\n"
        f"Unresolved: {_format_unresolved(unresolved)}\n"
        f'Candidate: "{fast_candidate}"\n'
        "Respond APPROVED: <reason> or OBJECTION: <reason>"
    )


def build_revision_prompt(
    state: ModulatorState,
    person: PersonProfile | None,
    previous_candidate: str,
    objection: str,
) -> str:
    template = _load_template("fast_path_revision.md")
    if template:
        prompt = template
        prompt = prompt.replace("{modulator_snapshot}", _format_modulator_snapshot(state))
        prompt = prompt.replace("{person_profile_summary}", _format_person_summary(person))
        prompt = prompt.replace("{previous_candidate}", previous_candidate)
        prompt = prompt.replace("{slow_path_objection}", objection)
        return prompt
    return (
        f'Revise this: "{previous_candidate}"\n'
        f'Objection: "{objection}"\n'
        "Keep your authentic voice."
    )


def build_arbiter_prompt(
    state: ModulatorState,
    fast_final: str,
    slow_final: str,
) -> str:
    template = _load_template("arbiter.md")
    if template:
        prompt = template
        prompt = prompt.replace("{modulator_snapshot}", _format_modulator_snapshot(state))
        prompt = prompt.replace("{fast_final}", fast_final)
        prompt = prompt.replace("{slow_final}", slow_final)
        return prompt
    return (
        f'Intuitive: "{fast_final}"\n'
        f'Reflective: "{slow_final}"\n'
        "Synthesize both positions."
    )


# ---------------------------------------------------------------------------
# Slow path response parsing
# ---------------------------------------------------------------------------

def parse_slow_path_response(text: str) -> tuple[bool, str, bool]:
    """Parse slow path output.

    Returns (approved, reason_or_objection, parsed_successfully).
    If unparseable, returns (False, text, False) — treat as objection, not auto-approve.
    """
    stripped = text.strip()
    upper = stripped.upper()

    if upper.startswith("APPROVED"):
        reason = stripped[len("APPROVED"):].lstrip(":").strip()
        return True, reason, True

    if upper.startswith("OBJECTION"):
        reason = stripped[len("OBJECTION"):].lstrip(":").strip()
        return False, reason, True

    # Fallback: look for keywords anywhere
    if "APPROVED" in upper and "OBJECTION" not in upper:
        return True, stripped, True
    if "OBJECTION" in upper:
        return False, stripped, True

    # Unparseable: treat as objection (not auto-approve)
    return False, stripped, False


# ---------------------------------------------------------------------------
# Inner Dialogue
# ---------------------------------------------------------------------------

class InnerDialogue:
    """Iterative fast/slow path deliberation engine."""

    def __init__(self, backend: LLMBackend | None = None) -> None:
        from core.dual_process.generator import MockLLMBackend
        self._backend = backend or MockLLMBackend()
        self._llm_calls = 0

    def deliberate(
        self,
        user_message: str,
        state: ModulatorState,
        person: PersonProfile | None = None,
        self_profile: SelfProfile | None = None,
        values: ValueHierarchy | None = None,
        memories: list[LongTermEntry] | None = None,
        unresolved: list[UnresolvedItem] | None = None,
        contagion_summary: str = "",
        short_term_summary: str = "",
    ) -> InnerDialogueTrace:
        """Run the deliberation loop. Returns full trace."""
        self._llm_calls = 0
        memories = memories or []
        unresolved = unresolved or []
        rounds: list[DialogueRound] = []

        # Determine max rounds from control dynamics
        max_rounds = self._max_rounds(state)

        # Calm message: skip inner dialogue entirely (0 LLM calls)
        # Master generator will produce the response from scratch
        if max_rounds == 0:
            return InnerDialogueTrace(
                rounds=[],
                final_candidate="",
                total_llm_calls=0,
                reached_deadlock=False,
                dominant_path="skip",
                tension_level=0.0,
            )

        # --- Round 1: Fast path generates ---
        fast_prompt = build_fast_path_prompt(state, person, contagion_summary, short_term_summary)
        fast_candidate = self._call_llm(fast_prompt, user_message)

        # If max_rounds == 1 (bypass), skip slow path entirely
        if max_rounds <= 1:
            rounds.append(DialogueRound(
                round_number=1,
                fast_path_candidate=fast_candidate,
                slow_path_evaluation="[bypassed — high arousal or low energy]",
                slow_path_approved=True,
            ))
            return InnerDialogueTrace(
                rounds=rounds,
                final_candidate=fast_candidate,
                total_llm_calls=self._llm_calls,
                reached_deadlock=False,
                dominant_path="fast",
                tension_level=0.0,
            )

        # Slow path evaluates
        slow_prompt = build_slow_path_prompt(
            state, person, self_profile, values, unresolved, memories, fast_candidate,
        )
        slow_response = self._call_llm(slow_prompt, user_message)
        approved, reason, parsed = parse_slow_path_response(slow_response)

        # Retry once on parse failure, then treat as objection
        if not parsed:
            slow_response = self._call_llm(slow_prompt, user_message)
            approved, reason, parsed = parse_slow_path_response(slow_response)
            if not parsed:
                approved = False
                reason = f"[parse failure] {reason}"

        rounds.append(DialogueRound(
            round_number=1,
            fast_path_candidate=fast_candidate,
            slow_path_evaluation=slow_response,
            slow_path_approved=approved,
            objection_reason=reason if not approved else None,
        ))

        if approved:
            # Round 1 approval: final candidate is always the fast-path output
            return self._build_trace(rounds, fast_candidate, "fast")

        # --- Round 2: Fast path revises ---
        if not self._should_continue(state, 2, max_rounds, rounds[-1]):
            return self._build_trace(rounds, fast_candidate, "fast")

        revision_prompt = build_revision_prompt(state, person, fast_candidate, reason)
        revised_candidate = self._call_llm(revision_prompt, user_message)

        slow_prompt_2 = build_slow_path_prompt(
            state, person, self_profile, values, unresolved, memories, revised_candidate,
        )
        slow_response_2 = self._call_llm(slow_prompt_2, user_message)
        approved_2, reason_2, parsed_2 = parse_slow_path_response(slow_response_2)

        # Retry once on parse failure
        if not parsed_2:
            slow_response_2 = self._call_llm(slow_prompt_2, user_message)
            approved_2, reason_2, parsed_2 = parse_slow_path_response(slow_response_2)
            if not parsed_2:
                approved_2 = False
                reason_2 = f"[parse failure] {reason_2}"

        rounds.append(DialogueRound(
            round_number=2,
            fast_path_candidate=revised_candidate,
            slow_path_evaluation=slow_response_2,
            slow_path_approved=approved_2,
            objection_reason=reason_2 if not approved_2 else None,
            revision_notes=f"Revised in response to: {reason}",
        ))

        if approved_2:
            return self._build_trace(rounds, revised_candidate, "slow")

        # --- Round 3: Arbiter (deadlock) ---
        if not self._should_continue(state, 3, max_rounds, rounds[-1]):
            return self._build_trace(rounds, revised_candidate, "fast", deadlock_no_arbiter=True)

        arbiter_prompt = build_arbiter_prompt(state, revised_candidate, slow_response_2)
        arbiter_response = self._call_llm(arbiter_prompt, user_message)

        rounds.append(DialogueRound(
            round_number=3,
            fast_path_candidate=arbiter_response,
            slow_path_evaluation="[arbiter synthesis]",
            slow_path_approved=True,
        ))

        return self._build_trace(rounds, arbiter_response, "arbiter", deadlock=True)

    def create_deadlock_item(self, trace: InnerDialogueTrace) -> UnresolvedItem | None:
        """Create an UnresolvedItem from a deadlock, if one occurred."""
        if not trace.reached_deadlock:
            return None

        last_objection = ""
        for r in reversed(trace.rounds):
            if r.objection_reason:
                last_objection = r.objection_reason
                break

        return UnresolvedItem(
            id=f"deadlock_{uuid.uuid4().hex[:8]}",
            source="dialogue_deadlock",
            description=f"Fast/slow disagreement: {last_objection[:120]}" if last_objection else "Inner dialogue deadlock",
            created_at=datetime.now(timezone.utc),
            intensity=max(0.4, min(0.6, trace.tension_level)),
            decay_rate=0.10,  # fast decay per spec
        )

    # ------------------------------------------------------------------
    # Control dynamics
    # ------------------------------------------------------------------

    def _max_rounds(self, state: ModulatorState) -> int:
        """Determine max deliberation rounds from emotional state."""
        # High arousal → fast path dominates, 1 round only
        if state.arousal > AROUSAL_BYPASS_THRESHOLD:
            return 1
        # Low energy → too tired, 1 round only
        if state.energy < ENERGY_BYPASS_THRESHOLD:
            return 1
        # Calm message → nothing charged, skip inner dialogue entirely (0 rounds)
        if state.arousal < CALM_AROUSAL_THRESHOLD and state.resolution < CALM_RESOLUTION_THRESHOLD:
            return 0
        # High resolution → slow path insists, allow all 3 rounds
        if state.resolution > RESOLUTION_INSIST_THRESHOLD:
            return MAX_ROUNDS
        # Default: up to 3
        return MAX_ROUNDS

    def _should_continue(
        self,
        state: ModulatorState,
        next_round: int,
        max_rounds: int,
        last_round: DialogueRound,
    ) -> bool:
        """Check if deliberation should continue to next round."""
        if next_round > max_rounds:
            return False
        if next_round > MAX_ROUNDS:
            return False
        if last_round.slow_path_approved:
            return False
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _call_llm(self, system_prompt: str, user_message: str) -> str:
        self._llm_calls += 1
        return self._backend.generate(system_prompt, user_message)

    def _build_trace(
        self,
        rounds: list[DialogueRound],
        final: str,
        dominant: str,
        deadlock: bool = False,
        deadlock_no_arbiter: bool = False,
    ) -> InnerDialogueTrace:
        """Construct the trace from completed rounds."""
        total_rounds = len(rounds)
        objection_count = sum(1 for r in rounds if not r.slow_path_approved)
        tension = min(1.0, objection_count / max(total_rounds, 1))

        reached_deadlock = deadlock or deadlock_no_arbiter
        resolution = "arbiter" if deadlock else None

        return InnerDialogueTrace(
            rounds=rounds,
            final_candidate=final,
            total_llm_calls=self._llm_calls,
            reached_deadlock=reached_deadlock,
            deadlock_resolution=resolution,
            dominant_path=dominant,
            tension_level=tension,
        )
