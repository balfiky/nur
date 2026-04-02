"""Tool memory coupling — makes tool episodes part of ongoing identity.

Handles:
  1. Short-term memory records for tool executions
  2. Long-term memory writes for salient tool episodes
  3. Self-observations from tool behavior
  4. Unresolved items from tool failures
  5. Trust deltas from tool outcomes

All logic is deterministic — zero LLM calls.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.types import (
    ActionVariables,
    EmotionalEvent,
    EventType,
    LongTermEntry,
    ModulatorState,
    ToolCategory,
    ToolDecision,
    ToolObservation,
    ToolResult,
    UnresolvedItem,
)

# ---------------------------------------------------------------------------
# Max summary length to prevent memory bloat
# ---------------------------------------------------------------------------

_MAX_MEMORY_SUMMARY = 200

# ---------------------------------------------------------------------------
# Salience thresholds for long-term memory writes
# ---------------------------------------------------------------------------

_CERTAINTY_SHIFT_THRESHOLD = 0.08  # |certainty_delta| above this is salient
_STRONG_EMOTION_VALENCE = 0.06     # |valence delta| above this is salient

# ---------------------------------------------------------------------------
# Trust deltas (conservative, respects existing asymmetry)
# ---------------------------------------------------------------------------

_TRUST_POSITIVE_TOOL = 0.01   # successful helpful action
_TRUST_NEGATIVE_TOOL = -0.03  # reckless or failed destructive action


# ---------------------------------------------------------------------------
# Tool memory effects — returned from processing for debug visibility
# ---------------------------------------------------------------------------

@dataclass
class ToolMemoryEffects:
    """What the tool memory coupling produced this turn."""
    short_term_recorded: bool = False
    long_term_written: bool = False
    long_term_summary: str = ""
    self_observations: list[str] = field(default_factory=list)
    unresolved_items_created: list[str] = field(default_factory=list)
    trust_delta: float = 0.0


# ---------------------------------------------------------------------------
# 1. Short-term memory record
# ---------------------------------------------------------------------------

def create_tool_event(
    result: ToolResult,
    observation: ToolObservation,
    state: ModulatorState,
) -> EmotionalEvent:
    """Create an EmotionalEvent representing a tool execution.

    This feeds into the existing short-term memory system.
    """
    intensity = 0.3  # baseline for any tool action
    if not result.success:
        intensity = 0.5
    if abs(observation.certainty_delta) > _CERTAINTY_SHIFT_THRESHOLD:
        intensity = max(intensity, 0.6)

    summary = _compact_summary(result, observation)

    return EmotionalEvent(
        event_type=EventType.USER_MESSAGE,
        intensity=min(1.0, intensity),
        source="tool",
        metadata={
            "type": "tool_execution",
            "tool_name": result.tool_name,
            "success": result.success,
            "summary": summary,
            "emotional_delta": observation.emotional_delta,
        },
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# 2. Long-term memory salience check + entry creation
# ---------------------------------------------------------------------------

def is_salient_episode(
    result: ToolResult,
    observation: ToolObservation,
    category: ToolCategory,
    failure_count: int = 0,
) -> bool:
    """Decide if this tool episode should be persisted long-term.

    Criteria:
      - Destructive actions (always salient)
      - Failed executions
      - Repeated failures (failure_count >= 2)
      - Strong emotional shift (certainty or valence)
      - Surprising: empty output on a normally productive tool
    """
    if category == ToolCategory.DESTRUCTIVE:
        return True
    if not result.success:
        return True
    if failure_count >= 2:
        return True
    if abs(observation.certainty_delta) >= _CERTAINTY_SHIFT_THRESHOLD:
        return True
    val_delta = abs(observation.emotional_delta.get("valence", 0.0))
    if val_delta >= _STRONG_EMOTION_VALENCE:
        return True
    return False


def create_long_term_entry(
    result: ToolResult,
    observation: ToolObservation,
    category: ToolCategory,
    user_id: str = "",
) -> LongTermEntry:
    """Create a long-term memory entry for a salient tool episode.

    Stores a compact summary, never raw output.
    """
    summary = _compact_summary(result, observation)
    # Emotional valence for the entry: positive if successful, negative if failed
    valence = 0.1 if result.success else -0.3
    if category == ToolCategory.DESTRUCTIVE:
        valence = -0.1 if result.success else -0.5

    return LongTermEntry(
        timestamp=time.time(),
        summary=summary,
        emotional_valence=valence,
        trust_delta=0.0,  # trust handled separately
        topic=f"tool:{result.tool_name}",
        source_person=user_id,
        confidence=0.8 if result.success else 0.9,  # failures are high-confidence memories
        spike=not result.success,  # failures bypass confidence threshold
    )


# ---------------------------------------------------------------------------
# 3. Self-observations from tool behavior
# ---------------------------------------------------------------------------

def derive_tool_self_observations(
    result: ToolResult,
    observation: ToolObservation,
    category: ToolCategory,
    action_vars: ActionVariables,
    decision: ToolDecision | None,
    failure_count: int = 0,
) -> list[tuple[str, float, str]]:
    """Derive self-observations from tool behavior.

    Returns list of (trait, value, context) tuples for the self-profile.
    """
    obs: list[tuple[str, float, str]] = []

    if result.success:
        # Methodical: successful read/search
        if category in (ToolCategory.READ_ONLY, ToolCategory.COGNITIVE):
            obs.append(("methodical", 0.6, f"tool_success:{result.tool_name}"))
        # Decisive: destructive action completed
        if category == ToolCategory.DESTRUCTIVE:
            obs.append(("decisive", 0.7, f"destructive_success:{result.tool_name}"))
        # Technically competent: any success
        obs.append(("technically_competent", 0.5, f"tool_success:{result.tool_name}"))
        # Reckless: acted with high risk tolerance on a write/destructive
        if category in (ToolCategory.WRITE, ToolCategory.DESTRUCTIVE):
            if action_vars.risk_tolerance > 0.7:
                obs.append(("reckless", action_vars.risk_tolerance, f"high_risk:{result.tool_name}"))
    else:
        # Frustrated: failure
        obs.append(("frustrated", 0.5, f"tool_failure:{result.tool_name}"))
        # Persistent: kept trying after failures
        if failure_count >= 2:
            obs.append(("persistent", 0.6, f"repeated_failure:{result.tool_name}"))

    # Hesitant: decided to clarify instead of act
    if decision and decision.decision == "clarify":
        obs.append(("hesitant", 0.4, f"clarified_first:{decision.intent.tool_name if decision.intent else 'unknown'}"))

    # Avoidant: refused or deferred
    if decision and decision.decision in ("refuse", "defer"):
        obs.append(("avoidant", 0.5, f"{decision.decision}:{decision.intent.tool_name if decision.intent else 'unknown'}"))

    # Cap at 3 per turn
    return obs[:3]


# ---------------------------------------------------------------------------
# 4. Unresolved items from tool outcomes
# ---------------------------------------------------------------------------

def create_tool_unresolved_item(
    result: ToolResult,
    observation: ToolObservation,
    category: ToolCategory,
    failure_count: int = 0,
) -> UnresolvedItem | None:
    """Create an unresolved item from a tool failure if warranted.

    Sources:
      - tool_failure: execution error
      - blocked_action: permission/environment block
      - incomplete_task: tool ran but produced no useful output
    """
    if result.success and result.output.strip():
        return None  # successful with output — nothing unresolved

    error_msg = result.error or ""
    is_block = any(
        kw in error_msg.lower()
        for kw in ["permission", "denied", "forbidden", "not allowed", "blocked"]
    )

    if is_block:
        source = "blocked_action"
        desc = f"Blocked: {result.tool_name} — {error_msg[:100]}"
        intensity = 0.4 + min(0.2, failure_count * 0.1)
        decay = 0.05
    elif not result.success:
        source = "tool_failure"
        desc = f"Failed: {result.tool_name} — {error_msg[:100]}"
        intensity = 0.3 + min(0.3, failure_count * 0.15)
        decay = 0.08
    elif result.success and not result.output.strip():
        source = "incomplete_task"
        desc = f"No useful output from {result.tool_name}"
        intensity = 0.2
        decay = 0.10
    else:
        return None

    return UnresolvedItem(
        id=f"{source}_{uuid.uuid4().hex[:8]}",
        source=source,
        description=desc,
        created_at=datetime.now(timezone.utc),
        intensity=min(1.0, intensity),
        decay_rate=decay,
    )


# ---------------------------------------------------------------------------
# 5. Trust delta from tool outcomes
# ---------------------------------------------------------------------------

def compute_tool_trust_delta(
    result: ToolResult,
    category: ToolCategory,
    action_vars: ActionVariables,
) -> float:
    """Compute a conservative trust delta from a tool outcome.

    - Successful helpful action → small positive
    - Failed destructive / reckless action → negative
    - Most tool actions → zero (neutral)
    """
    if result.success:
        # Only positive trust for clearly helpful actions
        if category in (ToolCategory.READ_ONLY, ToolCategory.COGNITIVE):
            return _TRUST_POSITIVE_TOOL
        return 0.0

    # Failures
    if category == ToolCategory.DESTRUCTIVE:
        return _TRUST_NEGATIVE_TOOL
    if action_vars.risk_tolerance > 0.7:
        return _TRUST_NEGATIVE_TOOL  # reckless failure
    return 0.0  # routine failure, no trust impact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compact_summary(result: ToolResult, observation: ToolObservation) -> str:
    """Build a compact summary suitable for memory storage."""
    if result.success:
        output_preview = result.output[:_MAX_MEMORY_SUMMARY].strip()
        if len(result.output) > _MAX_MEMORY_SUMMARY:
            output_preview += "..."
        return f"Tool {result.tool_name}: {output_preview}" if output_preview else f"Tool {result.tool_name}: completed (no output)"
    return f"Tool {result.tool_name} failed: {(result.error or 'unknown')[:_MAX_MEMORY_SUMMARY]}"
