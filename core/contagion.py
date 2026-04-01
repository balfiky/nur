"""Emotional contagion — detect user tone and mirror into modulators.

LLM-based detection with rule-based keyword fallback.
Returns DetectedEmotion (arousal, valence, certainty, intensity).
Mirroring is bounded +/-0.15 and weighted by bonding score.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from config.loader import get_config
from core.types import DetectedEmotion

# ---------------------------------------------------------------------------
# LLM backend protocol (same as generator.py)
# ---------------------------------------------------------------------------


class _LLMBackend(Protocol):
    def generate(self, system_prompt: str, user_message: str) -> str: ...


# ---------------------------------------------------------------------------
# LLM-based detection
# ---------------------------------------------------------------------------


def _detect_via_llm(text: str, llm: _LLMBackend) -> DetectedEmotion | None:
    """Call LLM to detect emotion. Returns None on failure (triggers fallback)."""
    prompt = get_config().contagion_prompt
    if not prompt:
        return None

    try:
        raw = llm.generate(prompt, text)
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return DetectedEmotion(
            arousal=float(data.get("arousal", 0.5)),
            valence=float(data.get("valence", 0.5)),
            certainty=float(data.get("certainty", 0.5)),
            intensity=float(data.get("intensity", 0.0)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Rule-based fallback (keyword lexicons)
# ---------------------------------------------------------------------------

# (pattern, valence_shift, arousal_shift)
_PATTERNS: list[tuple[str, float, float]] = [
    # === High arousal, negative valence ===

    # Direct insults and personal attacks
    (r"\b(stupid|idiot|moron|dumb|pathetic|loser|worthless|useless)\b", -0.8, 0.8),
    (r"\b(incompetent|clueless|brainless|ignorant|fool|foolish)\b", -0.7, 0.7),
    (r"\b(ugly|disgusting|repulsive|hideous|revolting)\b", -0.8, 0.7),

    # Profanity
    (r"\b(fuck|fucking|fucked|fucker)\b", -0.7, 0.85),
    (r"\b(shit|shitty|bullshit|horseshit)\b", -0.6, 0.8),
    (r"\b(damn|damned|goddamn)\b", -0.4, 0.6),
    (r"\b(ass|asshole|bastard|bitch)\b", -0.7, 0.8),
    (r"\b(crap|crappy|suck|sucks)\b", -0.5, 0.6),
    (r"\b(wtf|stfu|lmao|smh)\b", -0.5, 0.7),

    # Direct hostility / commands
    (r"\bshut\s*up\b", -0.85, 0.85),
    (r"\bgo\s+(away|to\s+hell)\b", -0.85, 0.85),
    (r"\bleave\s+me\s+alone\b", -0.7, 0.7),
    (r"\bget\s+(lost|out)\b", -0.8, 0.8),
    (r"\bpiss\s+off\b", -0.85, 0.85),
    (r"\bscrew\s+you\b", -0.85, 0.85),
    (r"\bhate\s+you\b", -0.9, 0.8),
    (r"\b(despise|detest|loathe)\b", -0.9, 0.8),

    # Rage and fury
    (r"\b(furious|enraged|livid)\b", -0.9, 0.9),
    (r"\b(angry|pissed|mad)\b", -0.7, 0.8),
    (r"\b(frustrated|annoyed|irritated)\b", -0.5, 0.6),
    (r"\b(anxious|nervous|panicking|freaking)\b", -0.4, 0.8),
    (r"\b(terrified|scared|afraid|frightened)\b", -0.6, 0.8),
    (r"\b(stressed|overwhelmed|burned\s*out)\b", -0.5, 0.7),

    # === High arousal, positive valence ===
    (r"\b(ecstatic|thrilled|elated|overjoyed)\b", 0.9, 0.9),
    (r"\b(excited|pumped|hyped|stoked)\b", 0.7, 0.8),
    (r"\b(amazing|awesome|fantastic|incredible|wonderful)\b", 0.7, 0.6),
    (r"\b(love|adore)\b", 0.8, 0.5),
    (r"\b(brilliant|outstanding|magnificent|superb|excellent)\b", 0.7, 0.6),

    # === Low arousal, negative valence ===
    (r"\b(sad|unhappy|miserable|depressed)\b", -0.7, 0.2),
    (r"\b(lonely|isolated|abandoned)\b", -0.6, 0.2),
    (r"\b(hopeless|defeated|given\s+up|give\s+up)\b", -0.8, 0.1),
    (r"\b(tired|exhausted|drained|wiped)\b", -0.3, 0.1),
    (r"\b(bored|meh|whatever)\b", -0.3, 0.3),
    (r"\b(disappointed|let\s+down)\b", -0.5, 0.3),

    # Contempt / dismissal
    (r"\bwho\s+cares\b", -0.5, 0.4),
    (r"\b(pointless|waste\s+of\s+time|don't\s+care)\b", -0.5, 0.4),
    (r"\b(terrible|horrible|awful|dreadful)\b", -0.7, 0.6),
    (r"\b(worst|atrocious|abysmal)\b", -0.8, 0.6),
    (r"\b(wrong|bad|broken|ruined)\b", -0.5, 0.5),

    # === Low arousal, positive valence ===
    (r"\b(calm|peaceful|serene|relaxed)\b", 0.4, 0.1),
    (r"\b(content|satisfied|pleased)\b", 0.5, 0.2),
    (r"\b(grateful|thankful|appreciate)\b", 0.6, 0.3),
    (r"\b(happy|glad|good)\b", 0.5, 0.3),
    (r"\b(hopeful|optimistic)\b", 0.5, 0.3),

    # Compliments and warmth
    (r"\b(smart|clever|talented|gifted|skilled)\b", 0.6, 0.3),
    (r"\b(kind|sweet|caring|gentle|thoughtful)\b", 0.6, 0.2),
    (r"\b(beautiful|lovely|pretty|gorgeous)\b", 0.6, 0.3),
    (r"\b(helpful|useful|valuable|impressive)\b", 0.5, 0.3),
    (r"\b(great\s+job|well\s+done|nice\s+work|good\s+job)\b", 0.7, 0.4),

    # === Conflict / trust signals ===
    (r"\b(betrayed|lied|deceived)\b", -0.9, 0.7),
    (r"\b(sorry|apologize|my\s+bad)\b", -0.2, 0.3),
    (r"\b(trust|believe\s+in)\b", 0.6, 0.3),
    (r"\b(thank\s*you|thanks)\b", 0.4, 0.2),

    # === Casual greetings (mild positive) ===
    (r"\b(hello|hey|hi|howdy|yo)\b", 0.2, 0.2),
    (r"\b(good\s+(morning|afternoon|evening))\b", 0.3, 0.2),
]

# Punctuation / style signals (loaded from config)
_cont_cfg = get_config().contagion_detection
_CAPS_RATIO_THRESHOLD = _cont_cfg.caps_ratio_threshold
_EXCLAMATION_BOOST = _cont_cfg.exclamation_boost
_QUESTION_AROUSAL = _cont_cfg.question_arousal
_ELLIPSIS_VALENCE = _cont_cfg.ellipsis_valence


def _detect_via_rules(text: str) -> DetectedEmotion:
    """Rule-based fallback using keyword/pattern matching."""
    if not text or not text.strip():
        return DetectedEmotion(arousal=0.5, valence=0.5)

    lower = text.lower()

    # Collect keyword matches
    valence_shifts: list[float] = []
    arousal_values: list[float] = []

    for pattern, v_shift, a_abs in _PATTERNS:
        matches = re.findall(pattern, lower)
        if matches:
            for _ in matches:
                valence_shifts.append(v_shift)
                arousal_values.append(a_abs)

    # Punctuation / style modifiers
    punct_arousal_boost = 0.0
    punct_valence_boost = 0.0

    exclamations = text.count("!")
    punct_arousal_boost += min(exclamations * _EXCLAMATION_BOOST, 0.4)

    questions = text.count("?")
    punct_arousal_boost += min(questions * _QUESTION_AROUSAL, 0.15)

    if "..." in text or "\u2026" in text:
        punct_valence_boost += _ELLIPSIS_VALENCE

    # Caps detection (ignore short messages)
    alpha_chars = [c for c in text if c.isalpha()]
    if len(alpha_chars) > 10:
        caps_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        if caps_ratio > _CAPS_RATIO_THRESHOLD:
            punct_arousal_boost += 0.4
            if valence_shifts and (sum(valence_shifts) / len(valence_shifts)) < 0:
                punct_valence_boost -= 0.15

    # Compute final scores
    if valence_shifts:
        avg_valence = sum(valence_shifts) / len(valence_shifts)
        valence = 0.5 + avg_valence * 0.5 + punct_valence_boost
        avg_arousal = sum(arousal_values) / len(arousal_values)
        arousal = avg_arousal + punct_arousal_boost

        # Certainty: how consistent are the signals?
        # If all keywords agree on direction, high certainty.
        if len(valence_shifts) >= 2:
            same_sign = all(v >= 0 for v in valence_shifts) or all(v <= 0 for v in valence_shifts)
            certainty = 0.8 if same_sign else 0.4
        else:
            certainty = 0.6  # single keyword = moderate certainty

        # Intensity: how many matches and how extreme?
        match_count = len(valence_shifts)
        avg_extremity = sum(abs(v) for v in valence_shifts) / match_count
        intensity = min(1.0, avg_extremity * min(match_count, 3) / 3.0)
    else:
        valence = 0.5 + punct_valence_boost
        arousal = 0.5 + punct_arousal_boost
        certainty = 0.3  # no keywords = low certainty
        intensity = punct_arousal_boost  # only punctuation signal

    return DetectedEmotion(
        arousal=max(0.0, min(1.0, arousal)),
        valence=max(0.0, min(1.0, valence)),
        certainty=max(0.0, min(1.0, certainty)),
        intensity=max(0.0, min(1.0, intensity)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_emotion(text: str, llm_client: _LLMBackend | None = None) -> DetectedEmotion:
    """Detect user emotional state from message text.

    Uses LLM when available, falls back to rule-based keyword matching.
    Returns DetectedEmotion with arousal and valence in [0.0, 1.0].
    """
    if not text or not text.strip():
        return DetectedEmotion(arousal=0.5, valence=0.5)

    # Try LLM first
    if llm_client is not None:
        result = _detect_via_llm(text, llm_client)
        if result is not None:
            return result

    # Fallback to rule-based
    return _detect_via_rules(text)
