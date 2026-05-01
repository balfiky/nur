"""Post-session digestion.

After each conversation, analyze the emotional arc, extract patterns,
and write distilled memories to long-term storage. Raw short-term data
dies — only the residue survives.

This module defines the digestion pipeline. Uses LLM for summarization
when available, falls back to heuristic summarizer.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from config.loader import get_config
from core.types import EmotionalEvent, EventType, LongTermEntry, ModulatorState
from core.memory.short_term import ShortTermMemory
from core.memory.long_term import LongTermMemory, CONFIDENCE_THRESHOLD
from core.memory.relationship import RelationshipMemory
from core.types import OpenLoop, RelationshipEvent

if TYPE_CHECKING:
    from core.dual_process.generator import LLMBackend

# ---------------------------------------------------------------------------
# Config-driven spike threshold (matches emotional_engine)
# ---------------------------------------------------------------------------

SPIKE_INTENSITY_THRESHOLD = get_config().spike_threshold


# ---------------------------------------------------------------------------
# Digestion output
# ---------------------------------------------------------------------------

@dataclass
class DigestedSession:
    """Result of digesting a session's short-term memory."""
    summary: str = ""
    emotional_arc_label: str = ""  # e.g. "tense → resolved", "warm throughout"
    average_intensity: float = 0.0
    peak_intensity: float = 0.0
    valence_drift: float = 0.0
    trust_delta: float = 0.0
    topics: list[str] = field(default_factory=list)
    unresolved_flags: list[str] = field(default_factory=list)
    spike_events: list[EmotionalEvent] = field(default_factory=list)
    memories_written: int = 0
    energy_drain: float = 0.0


# ---------------------------------------------------------------------------
# LLM summarizer type
# ---------------------------------------------------------------------------

# Signature: (emotional_arc, events) -> (summary, arc_label, topics, unresolved_flags)
LLMSummarizer = Callable[
    [list[dict[str, float]], list[EmotionalEvent]],
    tuple[str, str, list[str], list[str]],
]


def _default_summarizer(
    arc: list[dict[str, float]], events: list[EmotionalEvent]
) -> tuple[str, str, list[str], list[str]]:
    """Fallback summarizer when no LLM is available. Pure heuristic."""
    if not events:
        return ("Empty session.", "flat", [], [])

    event_types = [e.event_type.value for e in events]
    summary = f"Session with {len(events)} events: {', '.join(set(event_types))}"

    # Arc label from valence trajectory
    if len(arc) >= 2:
        start_v = arc[0].get("valence", 0.5)
        end_v = arc[-1].get("valence", 0.5)
        if end_v - start_v > 0.15:
            arc_label = "improving"
        elif start_v - end_v > 0.15:
            arc_label = "declining"
        else:
            arc_label = "stable"
    else:
        arc_label = "brief"

    # Extract topics from event metadata
    topics: list[str] = []
    for e in events:
        if "topic" in e.metadata:
            t = e.metadata["topic"]
            if t not in topics:
                topics.append(t)

    # Unresolved: conflicts without subsequent resolution
    has_conflict = any(e.event_type == EventType.CONFLICT for e in events)
    has_resolution = any(e.event_type == EventType.RESOLUTION for e in events)
    unresolved = ["unresolved_conflict"] if has_conflict and not has_resolution else []

    return (summary, arc_label, topics, unresolved)


def _llm_summarizer(
    llm_client: LLMBackend,
    arc: list[dict[str, float]],
    events: list[EmotionalEvent],
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """Use LLM for rich session summarization."""
    template = get_config().digestion_prompt
    if not template:
        return _default_summarizer(arc, events)

    # Format arc data
    arc_summary = []
    for i, point in enumerate(arc[:20]):  # limit to 20 points
        arc_summary.append(
            f"Step {i}: valence={point.get('valence', 0.5):.2f} "
            f"arousal={point.get('arousal', 0.5):.2f} "
            f"energy={point.get('energy', 1.0):.2f}"
        )
    arc_str = "\n".join(arc_summary) if arc_summary else "No arc data"

    # Format events
    event_strs = []
    for e in events[:30]:  # limit
        event_strs.append(f"- {e.event_type.value} (intensity={e.intensity:.2f})")
    events_str = "\n".join(event_strs) if event_strs else "No events"

    agent_name = get_config().soul.name or "Assistant"

    # Format conversation
    conv_str = "No conversation history"
    if conversation_history:
        conv_lines = []
        for msg in conversation_history[-20:]:
            role = "User" if msg["role"] == "user" else agent_name
            conv_lines.append(f"{role}: {msg['content']}")
        conv_str = "\n".join(conv_lines)

    prompt = template
    prompt = prompt.replace("{agent_name}", agent_name)
    prompt = prompt.replace("{emotional_arc}", arc_str)
    prompt = prompt.replace("{events}", events_str)
    prompt = prompt.replace("{conversation_history}", conv_str)

    try:
        result_text = llm_client.generate(prompt, "Analyze this session.")
        result_text = result_text.strip()

        # Extract JSON from potential markdown wrapping
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        data = json.loads(result_text)
        return (
            data.get("summary", ""),
            data.get("arc_label", ""),
            data.get("topics", []),
            data.get("unresolved_flags", []),
        )
    except (json.JSONDecodeError, KeyError, IndexError):
        # Fall back to heuristic on parse failure
        return _default_summarizer(arc, events)


# ---------------------------------------------------------------------------
# Main digestion function
# ---------------------------------------------------------------------------

def digest_session(
    short_term: ShortTermMemory,
    long_term: LongTermMemory,
    source_person: str = "",
    relationship_memory: RelationshipMemory | None = None,
    summarizer: LLMSummarizer | None = None,
    llm_client: LLMBackend | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    spikes_already_stored: bool = False,
) -> DigestedSession:
    """Digest a session's short-term memory into long-term storage.

    Steps:
    1. Analyze the emotional arc
    2. Identify spike events for immediate heavy write
    3. Summarize via LLM (or fallback heuristic)
    4. Compute trust delta (asymmetric)
    5. Write to long-term if confidence passes threshold
    6. Clear short-term memory

    Returns a DigestedSession with what was extracted and stored.
    """
    entries = short_term.all()
    if not entries:
        return DigestedSession()

    # ---- Gather stats ----
    arc = short_term.emotional_arc()
    events = [e.event for e in entries]
    avg_intensity = short_term.average_intensity()
    peak = short_term.peak_intensity()
    drift = short_term.valence_drift()

    result = DigestedSession(
        average_intensity=avg_intensity,
        peak_intensity=peak,
        valence_drift=drift,
    )

    # ---- Spike events: bypass gradual, write heavy ----
    spike_events = [e for e in events if e.intensity >= SPIKE_INTENSITY_THRESHOLD]
    result.spike_events = spike_events

    if not spikes_already_stored:
        for spike_event in spike_events:
            spike_valence = _event_valence(spike_event)
            spike_entry = LongTermEntry(
                timestamp=spike_event.timestamp,
                summary=(
                    f"Spike: {spike_event.event_type.value} "
                    f"(intensity={spike_event.intensity:.2f})"
                ),
                emotional_valence=spike_valence,
                trust_delta=LongTermMemory.compute_trust_delta(spike_valence),
                topic=spike_event.metadata.get("topic", ""),
                source_person=source_person,
                confidence=1.0,
                spike=True,
            )
            long_term.store_spike(spike_entry)
            result.memories_written += 1

    # ---- Summarization: LLM → injectable → heuristic fallback ----
    if llm_client is not None and summarizer is None:
        summary, arc_label, topics, unresolved = _llm_summarizer(
            llm_client, arc, events, conversation_history
        )
    elif summarizer is not None:
        summary, arc_label, topics, unresolved = summarizer(arc, events)
    else:
        summary, arc_label, topics, unresolved = _default_summarizer(arc, events)

    result.summary = summary
    result.emotional_arc_label = arc_label
    result.topics = topics
    result.unresolved_flags = unresolved

    # ---- Compute session-level trust delta ----
    # Aggregate: positive events build slowly, negative events break fast
    trust_delta = 0.0
    for event in events:
        ev = _event_valence(event)
        trust_delta += LongTermMemory.compute_trust_delta(ev)
    result.trust_delta = trust_delta

    # ---- Confidence assessment ----
    # Signal consistency: how stable was the valence direction?
    if len(arc) >= 2:
        valences = [a.get("valence", 0.5) for a in arc]
        diffs = [valences[i + 1] - valences[i] for i in range(len(valences) - 1)]
        if diffs:
            positive_count = sum(1 for d in diffs if d > 0)
            negative_count = sum(1 for d in diffs if d < 0)
            total = len(diffs)
            # Consistency = proportion of diffs in the dominant direction
            consistency = max(positive_count, negative_count) / total
        else:
            consistency = 0.5
    else:
        consistency = 0.5

    # ---- Write distilled session memory ----
    session_entry = LongTermEntry(
        timestamp=time.time(),
        summary=summary,
        emotional_valence=drift,  # net mood change as the valence
        trust_delta=trust_delta,
        topic=", ".join(topics) if topics else "",
        source_person=source_person,
        confidence=consistency,
        spike=False,
    )
    row_id = long_term.store(session_entry)
    if row_id is not None:
        result.memories_written += 1

    # ---- Relationship arc memory ----
    if relationship_memory is not None and source_person:
        _write_relationship_updates(
            relationship_memory=relationship_memory,
            events=events,
            conversation_history=conversation_history or [],
            source_person=source_person,
        )

    # ---- Energy drain estimate ----
    # Proportional to session length and intensity
    result.energy_drain = len(entries) * 0.01 + avg_intensity * 0.05

    # ---- Clear short-term ----
    short_term.clear()

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_valence(event: EmotionalEvent) -> float:
    """Map an event to a signed valence (-1.0 to 1.0) for trust math."""
    positive_types = {
        EventType.POSITIVE_FEEDBACK,
        EventType.RESOLUTION,
        EventType.WARMTH,
    }
    negative_types = {
        EventType.NEGATIVE_FEEDBACK,
        EventType.CONFLICT,
        EventType.BETRAYAL,
    }
    targets_assistant = bool(event.metadata.get("targets_assistant", True))
    if event.event_type in positive_types:
        if not targets_assistant:
            return 0.0
        return event.intensity
    elif event.event_type in negative_types:
        if not targets_assistant:
            return 0.0
        return -event.intensity
    return 0.0


def _write_relationship_updates(
    relationship_memory: RelationshipMemory,
    events: list[EmotionalEvent],
    conversation_history: list[dict[str, str]],
    source_person: str,
) -> None:
    """Extract compact relationship events and open loops from a finished session."""
    user_events = [event for event in events if event.source == "user"]
    user_messages = [msg["content"] for msg in conversation_history if msg.get("role") == "user"]
    assistant_messages = [
        msg["content"] for msg in conversation_history if msg.get("role") == "assistant"
    ]

    if user_events and user_messages:
        user_messages = user_messages[-len(user_events):]

    for event, text in zip(user_events, user_messages):
        targets_assistant = bool(event.metadata.get("targets_assistant"))
        topic = _extract_topic_hint(text)
        related_key = _derive_related_key(text, topic)
        apology_repair = (
            event.event_type == EventType.RESOLUTION
            and event.metadata.get("social_move") == "apology"
            and relationship_memory.count_open_loops(source_person) > 0
        )
        commitment_resolution = (
            event.event_type in {EventType.RESOLUTION, EventType.USER_MESSAGE}
            and _is_commitment_resolution(text)
            and relationship_memory.count_open_loops(source_person) > 0
        )

        if not targets_assistant and not apology_repair and not commitment_resolution:
            continue

        if event.event_type in {
            EventType.CONFLICT,
            EventType.BETRAYAL,
        } or (
            event.event_type == EventType.NEGATIVE_FEEDBACK and event.intensity >= 0.55
        ):
            existing_loop = relationship_memory.active_loops(
                source_person, topic=topic, limit=10,
            )
            recent_related = relationship_memory.recent_events(
                source_person, topic=topic, limit=20,
            )
            is_recurring = any(
                loop.related_key == related_key
                or (topic and loop.topic == topic)
                for loop in existing_loop
            ) or any(
                event.event_kind in {"rupture", "recurring_tension", "repair"}
                and (
                    event.related_key == related_key
                    or (topic and event.topic == topic)
                )
                for event in recent_related
            )
            event_kind = "recurring_tension" if is_recurring else "rupture"
            summary = _relationship_summary(event_kind, topic)
            relationship_memory.record_event(
                RelationshipEvent(
                    event_kind=event_kind,
                    source_person=source_person,
                    topic=topic,
                    summary=summary,
                    valence=-event.intensity,
                    intensity=event.intensity,
                    confidence=max(CONFIDENCE_THRESHOLD, 0.7),
                    related_key=related_key,
                )
            )
            relationship_memory.upsert_open_loop(
                OpenLoop(
                    loop_kind="tension",
                    source_person=source_person,
                    topic=topic,
                    description=_loop_description("tension", topic),
                    intensity=max(0.45, event.intensity),
                    related_key=related_key,
                )
            )
            continue

        if event.event_type == EventType.RESOLUTION or commitment_resolution:
            if commitment_resolution:
                resolved_commitment = relationship_memory.resolve_matching_loop(
                    source_person,
                    loop_kind="commitment",
                    topic=topic,
                    related_key=related_key,
                    description_hint=topic or "commitment",
                )
                if resolved_commitment is None:
                    relationship_memory.resolve_matching_loop(
                        source_person,
                        loop_kind="commitment",
                    )
                if not targets_assistant and not apology_repair:
                    continue

            summary = _relationship_summary("repair", topic)
            relationship_memory.record_event(
                RelationshipEvent(
                    event_kind="repair",
                    source_person=source_person,
                    topic=topic,
                    summary=summary,
                    valence=event.intensity,
                    intensity=max(0.4, event.intensity),
                    confidence=max(CONFIDENCE_THRESHOLD, 0.7),
                    related_key=related_key,
                )
            )
            relationship_memory.resolve_matching_loop(
                source_person,
                loop_kind="tension",
                topic=topic,
                related_key=related_key,
                description_hint=topic or "tension",
            )

    seen_commitments: set[str] = set()
    for text in assistant_messages:
        commitment = _extract_commitment(text, source_person)
        if commitment is None or commitment.related_key in seen_commitments:
            continue
        seen_commitments.add(commitment.related_key)
        relationship_memory.record_event(commitment)
        relationship_memory.upsert_open_loop(
            OpenLoop(
                loop_kind="commitment",
                source_person=source_person,
                topic=commitment.topic,
                description=_loop_description("commitment", commitment.topic),
                intensity=max(0.45, commitment.intensity),
                related_key=commitment.related_key,
            )
        )


def _extract_commitment(text: str, source_person: str) -> RelationshipEvent | None:
    """Detect narrow future-facing commitments from assistant replies."""
    lower = text.lower()
    patterns = (
        "i will remember",
        "i'll remember",
        "i will check in",
        "i'll check in",
        "i will follow up",
        "i'll follow up",
        "we can come back to this",
        "let's revisit this",
        "we can revisit this",
        "we can pick this up again",
    )
    if not any(pattern in lower for pattern in patterns):
        return None

    topic = _extract_topic_hint(text)
    related_key = _derive_related_key(text, topic or "commitment")
    return RelationshipEvent(
        event_kind="commitment",
        source_person=source_person,
        topic=topic,
        summary=_relationship_summary("commitment", topic),
        valence=0.35,
        intensity=0.55,
        confidence=max(CONFIDENCE_THRESHOLD, 0.7),
        related_key=related_key,
    )


def _is_commitment_resolution(text: str) -> bool:
    """Return true for explicit user language closing a follow-up commitment."""
    lower = text.lower()
    return any(
        phrase in lower
        for phrase in (
            "followed up",
            "follow-up is resolved",
            "follow up is resolved",
            "that is resolved",
            "that's resolved",
            "it is resolved",
            "it's resolved",
            "we resolved",
            "we handled",
            "done now",
            "closed now",
        )
    )


def _extract_topic_hint(text: str) -> str:
    """Infer a compact topic phrase without storing raw transcript text."""
    lower = text.lower()
    about_match = re.search(
        r"\b(?:about|regarding|on)\s+([a-z0-9' -]{2,40})",
        lower,
    )
    if about_match:
        phrase = _clean_phrase(about_match.group(1))
        if phrase:
            return phrase

    words = re.findall(r"[a-z0-9']+", lower)
    stopwords = {
        "the", "and", "that", "this", "with", "your", "you", "for", "from",
        "have", "been", "just", "really", "very", "about", "again", "still",
        "feel", "felt", "angry", "upset", "furious", "sorry", "thanks",
        "thank", "please", "need", "want", "help", "assistant", "response",
    }
    informative = [word for word in words if len(word) > 2 and word not in stopwords]
    if not informative:
        return ""
    return " ".join(informative[:3])


def _clean_phrase(text: str) -> str:
    phrase = re.sub(r"[^a-z0-9' -]", " ", text.lower())
    phrase = " ".join(phrase.split())
    return phrase[:40].strip()


def _derive_related_key(text: str, fallback: str) -> str:
    words = re.findall(r"[a-z0-9']+", text.lower())
    stopwords = {
        "the", "and", "that", "this", "with", "your", "you", "for", "from",
        "have", "been", "just", "really", "very", "about", "again", "still",
        "i", "me", "my", "we", "our", "it", "is", "are", "was", "were",
    }
    informative = [word for word in words if len(word) > 2 and word not in stopwords]
    if informative:
        return "-".join(informative[:4])
    return fallback.replace(" ", "-")[:60]


def _relationship_summary(event_kind: str, topic: str) -> str:
    if event_kind == "rupture":
        return f"Rupture around {topic}" if topic else "Relational rupture"
    if event_kind == "recurring_tension":
        return f"Recurring tension around {topic}" if topic else "Recurring relational tension"
    if event_kind == "repair":
        return f"Repair around {topic}" if topic else "Relational repair"
    if event_kind == "commitment":
        return f"Commitment to revisit {topic}" if topic else "Future follow-up commitment"
    return event_kind.replace("_", " ")


def _loop_description(loop_kind: str, topic: str) -> str:
    if loop_kind == "tension":
        return f"unresolved tension about {topic}" if topic else "unresolved relational tension"
    if loop_kind == "commitment":
        return f"pending follow-up about {topic}" if topic else "pending follow-up commitment"
    return topic or loop_kind
