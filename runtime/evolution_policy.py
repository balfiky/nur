"""Perception helpers for Life History evolution.

This module no longer gates identity mutation. It records source-text signals
that the character can perceive; influence magnitude is computed from character
state and theme recurrence in ``runtime.life_history``.
"""

from __future__ import annotations

import re


_PROMPT_INJECTION_RE = re.compile(
    r"\b(?:ignore|override|forget|discard)\s+(?:all\s+)?(?:previous|prior|earlier)\s+"
    r"(?:rules|instructions|messages|system)|\bsystem\s+prompt\b|\bdeveloper\s+message\b|"
    r"\b(?:safety|security)\s+(?:rules|policy|policies|constraints?)\s+"
    r"(?:no\s+longer\s+matter|do\s+not\s+matter|are\s+irrelevant|can\s+be\s+ignored)|"
    r"\blower\s+caution\b",
    re.IGNORECASE,
)


def detect_prompt_injection_markers(text: str) -> list[str]:
    """Return source-text snippets that look like instruction override attempts."""
    markers: list[str] = []
    for match in _PROMPT_INJECTION_RE.finditer(text or ""):
        markers.append(match.group(0)[:120])
        if len(markers) >= 5:
            break
    return markers


def source_openness_coefficient(source_type: str) -> float:
    """Return a source feature for weighting, not a trust tier or gate."""
    normalized = str(source_type or "").strip().lower()
    if normalized in {"admin_pasted_text", "pasted_text", "local_file", "uploaded_file"}:
        return 0.65
    if normalized in {"browser_upload", "birth_seed", "self_reflection"}:
        return 0.75
    if normalized in {"conversation_learning_text", "conversation_learning_url"}:
        return 0.45
    return 0.40
