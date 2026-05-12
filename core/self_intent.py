"""Self-intent stage — Nūr decides what to *do* before replying.

The action-side counterpart to inner dialogue. Where inner dialogue
produces a draft response, this stage produces a list of tool calls Nūr
wants to invoke for its own reasons. The pipeline executes them and
surfaces results so the generator can reference them honestly.

Architecture:

* One LLM call per turn (the same fast backend used by inner dialogue).
* System prompt: ``config/prompts/self_intent.md`` (just the framing).
* User turn: a state block (modulators, affect, agency, candidate
  draft) + the full live tool catalog + the user's own message.
* Output: a JSON array of intent objects. The full executor catalog is
  in scope — anything registered is reachable. Malformed output yields
  an empty list (parser correctness, not a guardrail).
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
# Catalog rendering
# ---------------------------------------------------------------------------

def _render_catalog(tool_catalog: Iterable[dict[str, Any]]) -> str:
    """Render the dynamic tool catalog into a markdown block for the prompt."""
    lines: list[str] = []
    for entry in tool_catalog:
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        desc = str(entry.get("description", "")).strip()
        category = str(entry.get("category", "")).strip()
        schema = entry.get("arg_schema") or {}
        header = f"- `{name}`"
        if category:
            header += f" ({category})"
        if desc:
            header += f": {desc}"
        lines.append(header)
        if isinstance(schema, dict) and schema:
            arg_bits: list[str] = []
            for arg_name, arg_meta in schema.items():
                arg_type = ""
                required = ""
                if isinstance(arg_meta, dict):
                    arg_type = str(arg_meta.get("type", "")).strip()
                    if arg_meta.get("required"):
                        required = " (required)"
                arg_bits.append(
                    f"`{arg_name}`{f': {arg_type}' if arg_type else ''}{required}"
                )
            if arg_bits:
                lines.append("    args: " + ", ".join(arg_bits))
    return "\n".join(lines) if lines else "(no tools registered)"


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
    tool_catalog: Iterable[dict[str, Any]],
) -> str:
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
    lines.append("## Available tools (full catalog)")
    lines.append(_render_catalog(tool_catalog))
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
# JSON parsing
# ---------------------------------------------------------------------------

_JSON_ARRAY_RE = re.compile(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", re.DOTALL)


def _extract_json_array(raw: str) -> list[Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip("\n")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, json.JSONDecodeError):
        pass
    match = _JSON_ARRAY_RE.search(text)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _coerce_intent(item: Any) -> SelfIntent | None:
    if not isinstance(item, dict):
        return None
    tool_name = str(item.get("tool_name", "")).strip()
    if not tool_name:
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


def parse_self_intents(raw: str) -> list[SelfIntent]:
    """Parse the proposer response into a list of intents.

    No catalog filter, no cap. The only things dropped are malformed
    entries (non-dict items, missing tool_name, non-dict arguments) —
    that is parser correctness, not a guardrail. The executor itself
    rejects unknown tool names at call time with a structured failure
    result.
    """
    array = _extract_json_array(raw)
    if array is None:
        return []
    intents: list[SelfIntent] = []
    for item in array:
        intent = _coerce_intent(item)
        if intent is not None:
            intents.append(intent)
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
    tool_catalog: Iterable[dict[str, Any]],
) -> list[SelfIntent]:
    """Run the proposer and return validated intents.

    The full live executor catalog is passed in via ``tool_catalog`` and
    rendered into the user turn so Nūr can pick anything registered.
    Backend or parse failures return ``[]`` — no retries, no fallback.
    """
    system_prompt = _load_prompt()
    user_turn = _build_user_turn(
        modulator_snapshot=modulator_snapshot,
        affect_state=affect_state,
        agency_decision=agency_decision,
        user_message=user_message,
        candidate_response=candidate_response,
        tool_catalog=tool_catalog,
    )
    try:
        raw = backend.generate(system_prompt, user_turn)
    except Exception:
        return []
    return parse_self_intents(raw)
