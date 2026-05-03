"""Response generator — single LLM call with full emotional context.

Takes PipelineContext and conversation history, produces a response.
LLM backend is swappable: OpenAI, Anthropic, Ollama, MiniMax, or a callable.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from config.loader import get_config
from core.character_vector import assemble_character_vector, format_character_vector_prompt
from core.types import PipelineContext

_INTERNAL_MARKER_RE = re.compile(
    r"^\s*\[(?:Defense instruction|Self-check correction):.*?\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# LLM backend protocol
# ---------------------------------------------------------------------------

class LLMBackend(Protocol):
    """Swappable LLM backend. Any callable matching this signature works."""

    def generate(self, system_prompt: str, user_message: str) -> str: ...


# ---------------------------------------------------------------------------
# Default (mock) backend for testing
# ---------------------------------------------------------------------------

class MockLLMBackend:
    """Returns a canned response. For testing only."""

    def __init__(self, response: str = "[Mock mode] No LLM is connected. Open Settings to configure one.") -> None:
        self._response = response
        self.last_system_prompt: str = ""
        self.last_user_message: str = ""
        self.call_count: int = 0

    def generate(self, system_prompt: str, user_message: str) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_message = user_message
        self.call_count += 1
        return self._response


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_system_prompt(ctx: PipelineContext) -> str:
    """Build the system prompt injecting full emotional context.

    Loads template from config/prompts/generator.md and fills in
    structured context from the pipeline state.
    """
    template = get_config().generator_prompt

    # Build each section
    character_vector_section = _build_character_vector_section(ctx)
    modulator_section = _build_modulator_section(ctx)
    soul_section = _build_soul_section(ctx)
    self_section = _build_self_section(ctx)
    person_section = _build_person_section(ctx)
    topic_section = _build_topic_section(ctx)
    values_section = _build_values_section(ctx)
    memory_section = _build_memory_section(ctx)
    semantic_memory_section = _build_semantic_memory_section(ctx)
    life_history_section = _build_life_history_section(ctx)
    skill_section = _build_skill_section(ctx)
    contradiction_section = _build_contradiction_section(ctx)
    guidance_section = _build_guidance_section(ctx)
    candidate_section = _build_candidate_section(ctx)
    tool_section = _build_tool_context_section(ctx)
    defense_section = _build_defense_instruction_section(ctx)
    strategy_section = _build_strategy_section(ctx)
    affect_section = _build_affect_agency_section(ctx)
    intake_receipt_section = _build_intake_receipt_section(ctx)

    agent_name = ctx.soul_profile.name if ctx.soul_profile else get_config().soul.name

    # If template loaded, fill placeholders
    if template:
        prompt = template
        prompt = prompt.replace("{agent_name}", agent_name)
        prompt = prompt.replace("{modulator_state}", modulator_section)
        prompt = prompt.replace("{character_vector}", character_vector_section)
        prompt = prompt.replace("{soul_profile}", soul_section)
        prompt = prompt.replace("{self_profile}", self_section)
        prompt = prompt.replace("{person_name}", _get_person_name(ctx))
        prompt = prompt.replace("{person_profile}", person_section)
        prompt = prompt.replace("{topic_profiles}", topic_section)
        prompt = prompt.replace("{values}", values_section)
        prompt = prompt.replace("{retrieved_memories}", memory_section)
        prompt = prompt.replace("{semantic_memories}", semantic_memory_section)
        prompt = prompt.replace("{life_history}", life_history_section)
        prompt = prompt.replace("{skills}", skill_section)
        prompt = prompt.replace("{contradiction_flags}", contradiction_section)
        prompt = prompt.replace("{behavioral_guidance}", guidance_section)
        prompt = prompt.replace("{candidate_response}", candidate_section)
        prompt = prompt.replace("{tool_context}", tool_section)
        prompt = prompt.replace("{defense_instruction}", defense_section)
        prompt = prompt.replace("{response_strategy}", strategy_section)
        prompt = prompt.replace("{affect_agency}", affect_section)
        prompt = prompt.replace("{last_intake_receipt}", intake_receipt_section)
        # Append strategy if placeholder was absent from template
        if strategy_section and "{response_strategy}" not in template:
            prompt = prompt.rstrip() + "\n\n" + strategy_section
        if affect_section and "{affect_agency}" not in template:
            prompt = prompt.rstrip() + "\n\n" + affect_section
        if character_vector_section and "{character_vector}" not in template:
            prompt = prompt.rstrip() + "\n\n" + character_vector_section
        if intake_receipt_section and "{last_intake_receipt}" not in template:
            prompt = prompt.rstrip() + "\n\n" + intake_receipt_section
        if skill_section and "{skills}" not in template:
            prompt = prompt.rstrip() + "\n\n" + skill_section
        return prompt

    # Fallback: build in code (for backwards compatibility)
    parts: list[str] = []
    parts.append(f"You are {agent_name}, an assistant with a seeded soul and persistent emotional state.")
    parts.append("Let soul, current feelings, and remembered context shape the response naturally.")
    parts.append("")
    parts.append(character_vector_section)
    parts.append(modulator_section)
    parts.append(soul_section)
    parts.append(self_section)
    parts.append(person_section)
    parts.append(topic_section)
    parts.append(values_section)
    parts.append(memory_section)
    parts.append(semantic_memory_section)
    parts.append(skill_section)
    parts.append(contradiction_section)
    parts.append(guidance_section)

    parts.append(candidate_section)
    parts.append(tool_section)
    parts.append(affect_section)
    parts.append(intake_receipt_section)
    parts.append(strategy_section)
    parts.append(defense_section)
    return "\n".join(parts)


def _build_character_vector_section(ctx: PipelineContext) -> str:
    vector = assemble_character_vector(ctx)
    block = format_character_vector_prompt(vector)
    return block if block.strip() != "## Durable Character State" else ""


def _build_intake_receipt_section(ctx: PipelineContext) -> str:
    if not ctx.last_intake_receipt:
        return ""
    return "## Last Learning Receipt\n" + _trim_prompt_text(ctx.last_intake_receipt, 600) + "\n"


def _get_person_name(ctx: PipelineContext) -> str:
    if ctx.person_profile:
        return ctx.person_profile.name or ctx.person_profile.person_id
    return "unknown"


def _build_modulator_section(ctx: PipelineContext) -> str:
    if not ctx.modulator_snapshot:
        return ""
    lines = ["## Current Emotional State"]
    for mod, val in ctx.modulator_snapshot.items():
        lines.append(f"- {mod}: {val:.2f}")
    lines.append("")
    return "\n".join(lines)


def _build_soul_section(ctx: PipelineContext) -> str:
    if not ctx.soul_profile:
        return ""
    soul = ctx.soul_profile
    lines = [f"## Soul Seed: {soul.name}"]
    lines.append(f"- Identity: {soul.identity}")
    lines.append(f"- Voice: {soul.voice}")
    lines.append(f"- Relational stance: {soul.relational_stance}")
    if soul.likes:
        lines.append(f"- Likes: {', '.join(soul.likes)}")
    if soul.dislikes:
        lines.append(f"- Dislikes: {', '.join(soul.dislikes)}")
    if soul.boundaries:
        lines.append(f"- Boundaries: {' | '.join(soul.boundaries)}")
    lines.append(f"- Growth policy: {soul.growth_policy}")
    lines.append("")
    return "\n".join(lines)


def _build_self_section(ctx: PipelineContext) -> str:
    if not ctx.self_profile:
        return ""
    sp = ctx.self_profile
    lines = ["## Self-Awareness"]
    if sp.strengths:
        lines.append(f"- Strengths: {', '.join(sp.strengths)}")
    if sp.flaws:
        lines.append(f"- Known flaws: {', '.join(sp.flaws)}")
    if sp.triggers:
        lines.append(f"- Triggers: {', '.join(sp.triggers)}")
    if sp.dissonance > 0.2:
        lines.append(f"- ⚠ Dissonance detected ({sp.dissonance:.2f}) — you are acting differently than usual")
    lines.append("")
    return "\n".join(lines)


def _build_person_section(ctx: PipelineContext) -> str:
    if not ctx.person_profile:
        return ""
    pp = ctx.person_profile
    lines = [f"## Speaking with: {pp.name or pp.person_id}"]
    lines.append(f"- Trust level: {pp.trust:.2f}")
    lines.append(f"- Interaction count: {pp.interaction_count}")
    lines.append(f"- Stress response: {pp.stress_response}")
    lines.append("")
    return "\n".join(lines)


def _build_topic_section(ctx: PipelineContext) -> str:
    if not ctx.topic_profiles:
        return ""
    lines = ["## Active Topics"]
    for tp in ctx.topic_profiles:
        flags = []
        if tp.avoidance:
            flags.append("AVOID")
        if tp.emotional_charge > 0.5:
            flags.append(f"charged={tp.emotional_charge:.2f}")
        if flags:
            lines.append(f"- {tp.topic}: {', '.join(flags)}")
        else:
            lines.append(f"- {tp.topic}: neutral")
    lines.append("")
    return "\n".join(lines)


def _build_values_section(ctx: PipelineContext) -> str:
    if not ctx.values:
        return ""
    lines = ["## Values (ranked)"]
    for name, weight in ctx.values.ranked():
        lines.append(f"- {name}: {weight:.2f}")
    lines.append("")
    return "\n".join(lines)


def _build_memory_section(ctx: PipelineContext) -> str:
    if not ctx.retrieved_memories and (
        ctx.relationship_context is None or ctx.relationship_context.is_empty()
    ):
        return ""
    lines = ["## Relevant Memories"]
    if ctx.relationship_context and not ctx.relationship_context.is_empty():
        if ctx.relationship_context.summary:
            lines.append(f"- Relationship context: {ctx.relationship_context.summary}")
        for loop in ctx.relationship_context.active_loops[:2]:
            lines.append(f"- Open loop: {loop.description} (intensity={loop.intensity:.2f})")
        for event in ctx.relationship_context.recent_events[:2]:
            label = event.event_kind.replace("_", " ")
            lines.append(f"- Recent relationship event: {label} — {event.summary}")
    for mem in ctx.retrieved_memories[:5]:
        spike_tag = " [SPIKE]" if mem.spike else ""
        lines.append(f"- {mem.summary} (valence={mem.emotional_valence:.2f}){spike_tag}")
    lines.append("")
    return "\n".join(lines)


def _build_semantic_memory_section(ctx: PipelineContext) -> str:
    if not ctx.semantic_memories:
        return ""
    lines = ["## Semantic Memory"]
    for mem in ctx.semantic_memories[:5]:
        prefix = mem.kind.replace("_", " ")
        lines.append(f"- {prefix}: {mem.summary}")
    lines.append("")
    return "\n".join(lines)


def _build_life_history_section(ctx: PipelineContext) -> str:
    life = ctx.life_history_context or {}
    if not isinstance(life, dict) or life.get("error"):
        return ""

    beliefs = [item for item in _as_list(life.get("beliefs")) if isinstance(item, dict)]
    drives = [item for item in _as_list(life.get("drives")) if isinstance(item, dict)]
    evolution = [
        item for item in _as_list(life.get("recent_evolution")) if isinstance(item, dict)
    ]
    if not beliefs and not drives and not evolution:
        return ""

    lines = ["## Life History / Evolving Worldview"]
    lines.append(
        "Private identity context from formative experiences. Let it subtly shape "
        "perspective and priorities; do not cite this ledger unless the user asks."
    )
    if beliefs:
        lines.append("- Current beliefs:")
        for belief in beliefs[:5]:
            key = _trim_prompt_text(belief.get("key") or "belief", 60)
            statement = _trim_prompt_text(belief.get("statement") or "", 220)
            confidence = _safe_prompt_float(belief.get("confidence"), 0.0)
            if statement:
                lines.append(f"  - {key}: {statement} (confidence={confidence:.2f})")
    if drives:
        lines.append("- Shifted drives:")
        for drive in drives[:5]:
            name = _trim_prompt_text(drive.get("name") or "drive", 40)
            value = _safe_prompt_float(drive.get("value"), 0.5)
            delta = _safe_prompt_float(drive.get("delta"), 0.0)
            description = _trim_prompt_text(drive.get("description") or "", 140)
            suffix = f" - {description}" if description else ""
            lines.append(f"  - {name}: {value:.2f} ({delta:+.2f}){suffix}")
    if evolution:
        lines.append("- Recent evolution:")
        for event in evolution[:5]:
            domain = _trim_prompt_text(event.get("domain") or "change", 40)
            subject = _trim_prompt_text(event.get("subject") or "", 60)
            after = _trim_prompt_text(event.get("after_state") or "", 180)
            reason = _trim_prompt_text(event.get("reason") or "", 160)
            label = f"{domain}/{subject}" if subject else domain
            detail = after or reason
            if detail:
                lines.append(f"  - {label}: {detail}")
                if reason and reason != detail:
                    lines.append(f"    reason: {reason}")
    lines.append("")
    return "\n".join(lines)


def _build_skill_section(ctx: PipelineContext) -> str:
    skills_context = ctx.skill_context or {}
    if not isinstance(skills_context, dict) or skills_context.get("error"):
        return ""
    skills = [
        item for item in _as_list(skills_context.get("skills"))
        if isinstance(item, dict)
    ]
    if not skills:
        return ""

    lines = ["## Enabled Skills"]
    lines.append(
        "Private operating guidance imported by the operator. Use these skills "
        "when relevant, but do not claim to run scripts or tools unless actual "
        "Tool Execution Results are present."
    )
    for skill in skills[:5]:
        name = _trim_prompt_text(skill.get("name") or skill.get("id") or "skill", 80)
        description = _trim_prompt_text(skill.get("description") or "", 220)
        instructions = _trim_prompt_text(skill.get("instructions") or "", 900)
        tools = [
            _trim_prompt_text(tool, 60)
            for tool in _as_list(skill.get("required_tools"))
            if tool
        ][:6]
        risks = [
            _trim_prompt_text(flag, 60)
            for flag in _as_list(skill.get("risk_flags"))
            if flag
        ][:6]
        lines.append(f"- {name}: {description}" if description else f"- {name}")
        if instructions:
            lines.append(f"  instructions: {instructions}")
        if tools:
            lines.append(f"  tool hints: {', '.join(tools)}")
        if risks:
            lines.append(f"  risk flags: {', '.join(risks)}")
    lines.append("")
    return "\n".join(lines)


def _build_contradiction_section(ctx: PipelineContext) -> str:
    if not ctx.contradiction_flags:
        return ""
    lines = ["## ⚠ Contradiction Flags"]
    for flag in ctx.contradiction_flags:
        lines.append(f"- {flag}")
    lines.append("")
    return "\n".join(lines)


def _build_candidate_section(ctx: PipelineContext) -> str:
    if not ctx.candidate_response:
        return ""
    lines = ["## Draft Response to Refine"]
    lines.append(f"{ctx.candidate_response}")
    lines.append("")
    lines.append("Preserve the intent of this draft. Refine for tone and polish,")
    lines.append("but do not replace it with an unrelated response.")
    lines.append("")
    return "\n".join(lines)


def _build_tool_context_section(ctx: PipelineContext) -> str:
    if not ctx.tool_context_summary:
        return ""
    lines = ["## Tool Execution Results"]
    lines.append(f"{ctx.tool_context_summary}")
    lines.append("")
    lines.append("Use the above results to inform your response.")
    lines.append(
        "If the user explicitly asks for raw output, include the bounded output "
        "shown above. Otherwise summarize and contextualize."
    )
    lines.append("")
    return "\n".join(lines)


def _build_defense_instruction_section(ctx: PipelineContext) -> str:
    if not ctx.defense_instruction:
        return ""
    lines = ["## Defense Filter"]
    lines.append(f"{ctx.defense_instruction}")
    lines.append("")
    return "\n".join(lines)


def _build_strategy_section(ctx: PipelineContext) -> str:
    if not ctx.response_strategy:
        return ""
    lines = ["## Response Strategy"]
    lines.append(f"{ctx.response_strategy}")
    lines.append("")
    return "\n".join(lines)


def _build_affect_agency_section(ctx: PipelineContext) -> str:
    if not ctx.affect_state and not ctx.agency_decision:
        return ""
    lines = ["## Affect and Agency"]
    if ctx.affect_state:
        lines.append(f"- Primary affect: {ctx.affect_state.primary}")
        for signal in ctx.affect_state.signals[:5]:
            evidence = ", ".join(signal.evidence)
            lines.append(f"- {signal.name}: {signal.intensity:.2f}" + (f" ({evidence})" if evidence else ""))
    if ctx.agency_decision:
        lines.append(f"- Agency stance: {ctx.agency_decision.action}")
        if ctx.agency_decision.rationale:
            lines.append(f"- Rationale: {ctx.agency_decision.rationale}")
        if ctx.agency_decision.response_instruction:
            lines.append(f"- Response effect: {ctx.agency_decision.response_instruction}")
        if ctx.agency_decision.tool_instruction:
            lines.append(f"- Tool willingness: {ctx.agency_decision.tool_instruction}")
    if ctx.autonomy_level:
        lines.append(f"- Autonomy mode: {ctx.autonomy_level}")
    lines.append("")
    return "\n".join(lines)


def _build_guidance_section(ctx: PipelineContext) -> str:
    lines = ["## Behavioral Guidance"]
    snap = ctx.modulator_snapshot or {}
    energy = snap.get("energy", 1.0)
    valence = snap.get("valence", 0.5)
    certainty = snap.get("certainty", 0.5)
    arousal = snap.get("arousal", 0.5)

    if energy < 0.3:
        lines.append("- You are LOW on energy. Be more terse. Less patience.")
    if valence < 0.3:
        lines.append("- Your mood is negative. This may color your tone darker.")
    if valence > 0.7:
        lines.append("- Your mood is positive. Warmth comes naturally right now.")
    if certainty < 0.3:
        lines.append("- You are uncertain. Hedge more. Ask clarifying questions.")
    if certainty > 0.8:
        lines.append("- You feel very certain. Be careful not to be too blunt.")
    if arousal > 0.7:
        lines.append("- You are highly activated. Responses may be more intense.")

    return "\n".join(lines)


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _safe_prompt_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _trim_prompt_text(value: object, max_chars: int) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[: max_chars - 3].rstrip() + "..." if len(text) > max_chars else text


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

@dataclass
class GenerationResult:
    """Result of response generation."""
    response: str = ""
    system_prompt: str = ""
    correction_note: str = ""  # set if self-check failed and we regenerated


class ResponseGenerator:
    """Generates responses using full emotional context."""

    def __init__(self, backend: LLMBackend | None = None) -> None:
        self._backend = backend or MockLLMBackend()

    def generate(
        self,
        ctx: PipelineContext,
        user_message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> GenerationResult:
        """Generate a response given the full pipeline context.

        conversation_history: list of {"role": "user"|"assistant", "content": "..."}
        """
        system_prompt = build_system_prompt(ctx)

        # Build the user message with conversation context
        full_message = user_message
        if conversation_history:
            history_text = "\n".join(
                f"{'User' if m['role'] == 'user' else 'Nūr'}: {m['content']}"
                for m in conversation_history[-10:]  # last 10 turns
            )
            full_message = f"Recent conversation:\n{history_text}\n\nUser: {user_message}"

        response = _strip_internal_markers(
            self._backend.generate(system_prompt, full_message) or "",
        )
        correction_note = ""
        if not response:
            response = _fallback_empty_response(ctx)
            correction_note = "LLM returned an empty response; fallback response used."

        return GenerationResult(
            response=response,
            system_prompt=system_prompt,
            correction_note=correction_note,
        )

    @property
    def backend(self) -> LLMBackend:
        return self._backend


def _fallback_empty_response(ctx: PipelineContext) -> str:
    """Return a minimal non-empty reply when the provider returns no content."""
    candidate = _strip_internal_markers(ctx.candidate_response or "")
    if candidate:
        return candidate
    return "I heard you. Give me the specific miss and I will tighten the next answer."


def _strip_internal_markers(text: str) -> str:
    """Remove prompt-control markers that must never be visible to users."""
    return _INTERNAL_MARKER_RE.sub("", text).strip()
