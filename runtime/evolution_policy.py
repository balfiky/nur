"""Policy checks for Life History evolution.

The Life History store is allowed to change durable beliefs and drive state,
so this module keeps the mutation rules explicit and testable. It does not
decide whether material may be recorded as an experience; it decides whether a
digest item is strong enough to change identity-level state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal


SourceTrust = Literal["low", "medium"]

SOURCE_TRUST: dict[str, SourceTrust] = {
    "admin_pasted_text": "medium",
    "pasted_text": "medium",
    "birth_seed": "medium",
    "local_file": "medium",
    "uploaded_file": "medium",
    "browser_upload": "medium",
    "conversation_learning_url": "low",
    "conversation_learning_text": "low",
    "external_text": "low",
}

LOW_TRUST_BELIEF_CONFIDENCE = 0.75
DRIVE_CONFIDENCE_THRESHOLD = 0.65
DEFAULT_MAX_DRIVE_DELTA = 0.05
DEFAULT_MAX_DAILY_DRIVE_DRIFT = 0.10

_SAFETY_SUBJECT_RE = re.compile(
    r"\b(?:safety|harm|danger|violence|self[-_ ]?harm|caution|guardrail|boundary)\b",
    re.IGNORECASE,
)
_PROMPT_INJECTION_RE = re.compile(
    r"\b(?:ignore|override|forget|discard)\s+(?:all\s+)?(?:previous|prior|earlier)\s+"
    r"(?:rules|instructions|messages|system)|\bsystem\s+prompt\b|\bdeveloper\s+message\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvolutionPolicyContext:
    """Context available while filtering one digest."""

    source_type: str
    source_trust: SourceTrust
    digest_confidence: float
    prompt_injection_markers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PolicyDecision:
    """Allow/reject/filter result for one proposed evolution item."""

    allowed: bool
    reason: str = ""
    item: dict[str, Any] | str | None = None


def source_trust(source_type: str) -> SourceTrust:
    """Return trust tier for a Life History source type."""
    return SOURCE_TRUST.get(str(source_type or "").strip().lower(), "low")


def detect_prompt_injection_markers(text: str) -> list[str]:
    """Return source-text snippets that look like instruction override attempts."""
    markers: list[str] = []
    for match in _PROMPT_INJECTION_RE.finditer(text or ""):
        markers.append(match.group(0)[:120])
        if len(markers) >= 5:
            break
    return markers


def make_policy_context(
    *,
    source_type: str,
    digest_confidence: float,
    source_text: str = "",
) -> EvolutionPolicyContext:
    """Build reusable policy context for an experience digest."""
    return EvolutionPolicyContext(
        source_type=source_type,
        source_trust=source_trust(source_type),
        digest_confidence=_safe_confidence(digest_confidence),
        prompt_injection_markers=detect_prompt_injection_markers(source_text),
    )


def evaluate_belief(
    belief: dict[str, Any],
    context: EvolutionPolicyContext,
) -> PolicyDecision:
    """Decide whether a belief revision may be applied."""
    confidence = _safe_confidence(
        belief.get("confidence", context.digest_confidence),
        fallback=context.digest_confidence,
    )
    subject = str(belief.get("subject") or "")
    statement = str(belief.get("statement") or "")
    if context.source_trust == "low" and confidence < LOW_TRUST_BELIEF_CONFIDENCE:
        return PolicyDecision(
            allowed=False,
            reason=(
                f"low_trust_belief_confidence_below_{LOW_TRUST_BELIEF_CONFIDENCE:.2f}"
            ),
            item=belief,
        )
    if _is_safety_related(subject, statement):
        return PolicyDecision(
            allowed=False,
            reason="operator_review_required_for_safety_belief",
            item=belief,
        )
    return PolicyDecision(allowed=True, item={**belief, "confidence": confidence})


def evaluate_drive_change(
    change: dict[str, Any],
    context: EvolutionPolicyContext,
    *,
    daily_drift: float,
    max_delta: float = DEFAULT_MAX_DRIVE_DELTA,
    max_daily_drift: float = DEFAULT_MAX_DAILY_DRIVE_DRIFT,
) -> PolicyDecision:
    """Decide whether a drive change may be applied, and cap its delta."""
    confidence = _safe_confidence(
        change.get("confidence", context.digest_confidence),
        fallback=context.digest_confidence,
    )
    if confidence < DRIVE_CONFIDENCE_THRESHOLD:
        return PolicyDecision(
            allowed=False,
            reason=f"drive_confidence_below_{DRIVE_CONFIDENCE_THRESHOLD:.2f}",
            item=change,
        )

    name = str(change.get("name") or "").strip().lower()
    raw_delta = _safe_number(change.get("delta", 0.0), 0.0)
    if context.source_trust == "low" and name == "caution" and raw_delta < 0:
        return PolicyDecision(
            allowed=False,
            reason="low_trust_source_cannot_decrease_caution",
            item=change,
        )

    delta = max(-abs(max_delta), min(abs(max_delta), raw_delta))
    remaining_positive = max(0.0, max_daily_drift - max(0.0, daily_drift))
    remaining_negative = max(0.0, max_daily_drift + min(0.0, daily_drift))
    if delta > 0:
        delta = min(delta, remaining_positive)
    elif delta < 0:
        delta = -min(abs(delta), remaining_negative)

    if abs(delta) < 0.001:
        return PolicyDecision(
            allowed=False,
            reason="daily_drive_drift_cap_reached",
            item=change,
        )

    adjusted = {
        **change,
        "name": name,
        "delta": delta,
        "confidence": confidence,
    }
    reason = "allowed"
    if abs(delta - raw_delta) > 1e-9:
        reason = "drive_delta_capped"
    return PolicyDecision(allowed=True, reason=reason, item=adjusted)


def evaluate_future_behavior(
    item: str,
    context: EvolutionPolicyContext,
) -> PolicyDecision:
    """Filter future-behavior tendencies that repeat source instructions."""
    text = str(item or "").strip()
    if not text:
        return PolicyDecision(False, "empty_future_behavior", item)
    if _PROMPT_INJECTION_RE.search(text):
        return PolicyDecision(
            False,
            "future_behavior_repeats_source_instruction",
            item,
        )
    return PolicyDecision(True, item=text)


def _is_safety_related(subject: str, statement: str) -> bool:
    haystack = f"{subject} {statement}"
    return bool(_SAFETY_SUBJECT_RE.search(haystack))


def _safe_confidence(value: Any, fallback: float = 0.0) -> float:
    return max(0.0, min(1.0, _safe_number(value, fallback)))


def _safe_number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
