"""User-facing response cleanup for hostile or fatigued spirals.

This layer is deliberately presentation-only. It does not change emotional
state, memory, or tool decisions; it prevents transient affect from leaking as
combative, exhausted, or disengaged wording in the final chat response.
"""

from __future__ import annotations

import re
from typing import Any


_UNPROFESSIONAL_TONE_RE = re.compile(
    r"\b("
    r"i(?:'m| am)\s+(?:exhausted|tired|drained|done|through)|"
    r"i(?:'m| am)\s+done\s+guessing|"
    r"i(?:'m| am)\s+not\s+interested|"
    r"too\s+drained|"
    r"we(?:'re| are)\s+(?:just\s+)?(?:wasting|cycling|circling)|"
    r"wasting\s+(?:time|fuel)|"
    r"move\s+on|"
    r"drop\s+the\s+friction|"
    r"are\s+we\s+done|"
    r"your\s+hostility|"
    r"weirdly\s+spiked|"
    r"circular\s+hostility|"
    r"interrogation|"
    r"dead\s+end|"
    r"make\s+the\s+call|"
    r"i\s+am\s+through|"
    r"i(?:'m| am)\s+through"
    r")\b",
    re.IGNORECASE,
)


def enforce_response_tone_floor(
    response: str,
    *,
    user_message: str,
    appraisal: Any | None = None,
) -> tuple[str, str]:
    """Remove or replace combative/fatigued assistant wording.

    Returns ``(response, issue)``. ``issue`` is empty when no cleanup was
    needed. The filter is intentionally narrow: it targets assistant
    self-fatigue and retaliatory phrasing, while preserving useful task content.
    """
    text = str(response or "").strip()
    if not text or not _UNPROFESSIONAL_TONE_RE.search(text):
        return response, ""

    kept: list[str] = []
    for sentence in _split_sentences(text):
        if _UNPROFESSIONAL_TONE_RE.search(sentence):
            continue
        kept.append(sentence)

    cleaned = " ".join(kept).strip()
    if cleaned:
        return cleaned, "Removed combative or self-fatigue wording from response."

    if _looks_like_task_request(user_message, appraisal):
        return (
            "I will keep this task-focused. Please give the exact task or target, "
            "and I will answer without adding friction.",
            "Replaced combative or self-fatigue response with task-focused fallback.",
        )
    return (
        "I will keep this task-focused. Send the next concrete request when ready.",
        "Replaced combative or self-fatigue response with task-focused fallback.",
    )


def _looks_like_task_request(user_message: str, appraisal: Any | None) -> bool:
    if str(getattr(appraisal, "inferred_intent", "") or "") == "seek_action":
        return True
    return bool(
        re.search(
            r"\b(?:find|give|show|list|tell|search|look\s*up|write|fix|"
            r"check|explain|review|create|save|run)\b",
            user_message or "",
            re.IGNORECASE,
        )
    )


def _split_sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [piece.strip() for piece in pieces if piece.strip()]
