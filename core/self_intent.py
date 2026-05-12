"""Self-intent stage — Nūr decides what to *do* before replying.

This is the action-side counterpart to inner dialogue. Where inner
dialogue produces a draft response, the self-intent stage produces a
bounded list of ``self.*`` tool calls Nūr wants to invoke for its own
reasons (witness, log, remember, verify, alert). The pipeline executes
them and surfaces results so the generator can reference them honestly.

Architecture:

* One LLM call per turn (the same fast backend used by inner dialogue).
* System prompt: ``config/prompts/self_intent.md`` (rules + examples).
* User turn: a small state block (modulators, affect, agency, candidate
  draft) followed by the user's own message.
* Output: a JSON array of intent objects. Anything not in the bounded
  catalog is dropped. Malformed output yields an empty list — no
  retries, no fallback, no bravado.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable

from core.dual_process.generator import LLMBackend
from core.types import AffectState, AgencyDecision, SelfIntent


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

_PROMPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "prompts",
)
_PROMPT_PATH = os.path.join(_PROMPT_DIR, "self_intent.md")
_PROMPT_CACHE: str | None = None


def _load_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        with open(_PROMPT_PATH, "r") as f:
            _PROMPT_CACHE = f.read()
    return _PROMPT_CACHE


# ---------------------------------------------------------------------------
# State block (per-turn context handed to the proposer)
# ---------------------------------------------------------------------------

def _format_modulators(snapshot: dict[str, float]) -> str:
    keys = ("arousal", "valence", "certainty", "bonding", "energy", "resolution")
    parts = [f"{k}={float(snapshot.get(k, 0.0)):.2f}" for k in keys if k in snapshot]
    return ", ".join(parts) or "n/a"


def _build_user_turn(
    *,
    modulator_snapshot: dict[str, float],
    affect_state: AffectState | None,
    agency_decision: AgencyDecision | None,
    user_message: str,
    candidate_response: str,
    available_tools: Iterable[str],
) -> str:
    available = sorted(set(available_tools))
    primary = getattr(affect_state, "primary", "") if affect_state else ""
    stance = getattr(agency_decision, "action", "") if agency_decision else ""
    candidate_excerpt = (candidate_response or "").strip()
    if len(candidate_excerpt) > 400:
        candidate_excerpt = candidate_excerpt[:397].rstrip() + "..."
    user_excerpt = (user_message or "").strip()
    if len(user_excerpt) > 800:
        user_excerpt = user_excerpt[:797].rstrip() + "..."

    lines = ["## Current state"]
    lines.append(f"Modulators: {_format_modulators(modulator_snapshot)}")
    if primary:
        lines.append(f"Primary affect: {primary}")
    if stance:
        lines.append(f"Agency stance: {stance}")
    lines.append("")
    lines.append("## Available self-actions")
    lines.append(", ".join(available) if available else "(none registered)")
    lines.append("")
    lines.append("## User message")
    lines.append(user_excerpt or "(empty)")
    lines.append("")
    lines.append("## Candidate response (draft, not final)")
    lines.append(candidate_excerpt or "(empty)")
    lines.append("")
    lines.append("Return ONLY a JSON array of action objects.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON parsing — extracts the array even if the LLM wraps it
# ---------------------------------------------------------------------------

_JSON_ARRAY_RE = re.compile(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", re.DOTALL)


def _extract_json_array(raw: str) -> list[Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Strip common markdown fences.
    if text.startswith("```"):
        text = text.strip("`")
        # Drop a possible "json\n" prefix after the fence.
        if text.lower().startswith("json"):
            text = text[4:].lstrip("\n")
    # Try direct parse first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, json.JSONDecodeError):
        pass
    # Fall back to a regex slice (covers prose preambles).
    match = _JSON_ARRAY_RE.search(text)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _coerce_intent(
    item: Any,
    available_tools: frozenset[str],
) -> SelfIntent | None:
    if not isinstance(item, dict):
        return None
    tool_name = str(item.get("tool_name", "")).strip()
    if tool_name not in available_tools:
        return None
    arguments = item.get("arguments", {})
    if not isinstance(arguments, dict):
        return None
    rationale = str(item.get("rationale", "")).strip()
    return SelfIntent(
        tool_name=tool_name,
        arguments=dict(arguments),
        rationale=rationale,
    )


def parse_self_intents(
    raw: str,
    available_tools: frozenset[str],
    max_intents: int,
) -> list[SelfIntent]:
    """Parse a proposer response into validated SelfIntent objects."""
    if max_intents <= 0:
        return []
    array = _extract_json_array(raw)
    if array is None:
        return []
    intents: list[SelfIntent] = []
    for item in array:
        intent = _coerce_intent(item, available_tools)
        if intent is None:
            continue
        intents.append(intent)
        if len(intents) >= max_intents:
            break
    return intents


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def propose_self_intents(
    backend: LLMBackend,
    *,
    modulator_snapshot: dict[str, float],
    affect_state: AffectState | None,
    agency_decision: AgencyDecision | None,
    user_message: str,
    candidate_response: str,
    available_tools: frozenset[str],
    max_intents: int = 3,
) -> list[SelfIntent]:
    """Run the self-intent proposer and return validated intents.

    Returns ``[]`` for any failure mode (no tools, LLM error, malformed
    JSON, empty array). No exceptions propagate.
    """
    if not available_tools or max_intents <= 0:
        return []
    system_prompt = _load_prompt()
    user_turn = _build_user_turn(
        modulator_snapshot=modulator_snapshot,
        affect_state=affect_state,
        agency_decision=agency_decision,
        user_message=user_message,
        candidate_response=candidate_response,
        available_tools=available_tools,
    )
    try:
        raw = backend.generate(system_prompt, user_turn)
    except Exception:
        return []
    return parse_self_intents(raw, available_tools, max_intents)
