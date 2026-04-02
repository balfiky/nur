"""Tool-aware deliberation bridge.

Coordinates the full cognitive tool loop:
  1. Detect if the user message warrants tool use (heuristic)
  2. Derive action variables from modulator state
  3. Build a ToolIntent
  4. Run the action arbiter → ToolDecision
  5. Execute the tool if approved
  6. Appraise the result → ToolObservation
  7. Apply emotional deltas to the engine
  8. Optionally loop (bounded)
  9. Return ToolTrace + context summary for the generator

All tool intent detection in this phase is deterministic heuristic.
Full LLM-driven structured proposals are a future enhancement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.action_variables import derive_action_variables
from core.tool_appraisal import appraise_tool_result
from core.types import (
    ActionVariables,
    ModulatorState,
    PersonProfile,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    ToolTrace,
)
from tools.executor import ToolExecutor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_EXECUTIONS = 2
HARD_CAP_EXECUTIONS = 3


# ---------------------------------------------------------------------------
# Tool loop result
# ---------------------------------------------------------------------------

@dataclass
class ToolLoopResult:
    """What the tool loop returns to the pipeline."""
    trace: ToolTrace
    action_variables: ActionVariables
    tool_context_summary: str  # summarized for the generator — not raw output


# ---------------------------------------------------------------------------
# Heuristic tool intent detection
# ---------------------------------------------------------------------------

# Patterns: (compiled_regex, tool_name, arg_extractor_name)
_TOOL_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Filesystem
    (re.compile(r"\bread(?:ing)?\s+(?:the\s+)?file\s+(\S+)", re.I), "fs.read_file", "path"),
    (re.compile(r"\b(?:show|cat|display)\s+(?:the\s+)?(?:contents?\s+of\s+)?(\S+\.\w+)", re.I), "fs.read_file", "path"),
    (re.compile(r"\blist\s+(?:the\s+)?(?:files?\s+in\s+|dir(?:ectory)?\s+)(\S+)", re.I), "fs.list_dir", "path"),
    (re.compile(r"\b(?:ls)\s+(\S+)", re.I), "fs.list_dir", "path"),
    (re.compile(r"\bsearch\s+(?:for\s+)?[\"']([^\"']+)[\"']\s+in\s+(\S+)", re.I), "fs.search_text", "pattern_path"),
    (re.compile(r"\bgrep\s+[\"']?(\S+)[\"']?\s+(\S+)", re.I), "fs.search_text", "pattern_path"),
    (re.compile(r"\bfind\s+files?\s+(?:matching\s+)?[\"']?([^\"'\s]+)[\"']?\s+in\s+(\S+)", re.I), "fs.glob_paths", "pattern_path"),
    (re.compile(r"\b(?:write|save)\s+[\"']([^\"']+)[\"']\s+to\s+(?:file\s+)?(\S+)", re.I), "fs.write_file", "content_path"),
    (re.compile(r"\bcreate\s+(?:a\s+)?file\s+(\S+)\s+(?:with|containing)\s+[\"']([^\"']+)[\"']", re.I), "fs.write_file", "path_content"),
    (re.compile(r"\bdelete\s+(?:the\s+)?(?:file|dir(?:ectory)?)\s+(\S+)", re.I), "fs.delete_path", "path"),
    (re.compile(r"\brm\s+(\S+)", re.I), "fs.delete_path", "path"),
    # Shell
    (re.compile(r"\brun\s+(?:the\s+)?(?:command\s+)?[`\"']([^`\"']+)[`\"']", re.I), "shell.run_command", "cmd"),
    (re.compile(r"\bexecute\s+[`\"']([^`\"']+)[`\"']", re.I), "shell.run_command", "cmd"),
    # Web
    (re.compile(r"\bsearch\s+(?:the\s+)?web\s+for\s+[\"']?([^\"']+?)[\"']?\s*$", re.I), "web.search", "query"),
    (re.compile(r"\blook\s*up\s+[\"']?([^\"']+?)[\"']?\s+online", re.I), "web.search", "query"),
    (re.compile(r"\bfetch\s+(?:the\s+)?(?:url\s+|page\s+(?:at\s+)?)?(https?://\S+)", re.I), "web.fetch", "url"),
]


def detect_tool_intent(
    user_message: str,
    available_tools: set[str],
) -> ToolIntent | None:
    """Detect if a user message warrants tool use via heuristics.

    Returns a ToolIntent if an obvious action request is found,
    None for conversational messages.
    """
    for pattern, tool_name, extractor in _TOOL_PATTERNS:
        if tool_name not in available_tools:
            continue
        m = pattern.search(user_message)
        if m:
            args = _extract_args(m, tool_name, extractor)
            if args is not None:
                return ToolIntent(
                    tool_name=tool_name,
                    arguments=args,
                    reason=f"User requested: {tool_name}",
                    expected_outcome=f"Execute {tool_name}",
                )
    return None


def _extract_args(
    match: re.Match, tool_name: str, extractor: str,
) -> dict[str, Any] | None:
    """Extract structured arguments from a regex match."""
    groups = match.groups()
    if not groups:
        return None

    if extractor == "path":
        return {"path": groups[0]}
    elif extractor == "cmd":
        return {"cmd": groups[0]}
    elif extractor == "query":
        return {"query": groups[0]}
    elif extractor == "url":
        return {"url": groups[0]}
    elif extractor == "pattern_path" and len(groups) >= 2:
        return {"pattern": groups[0], "path": groups[1]}
    elif extractor == "content_path" and len(groups) >= 2:
        return {"content": groups[0], "path": groups[1]}
    elif extractor == "path_content" and len(groups) >= 2:
        return {"path": groups[0], "content": groups[1]}
    return None


# ---------------------------------------------------------------------------
# Action arbiter — cognitive decision layer
# ---------------------------------------------------------------------------

def make_tool_decision(
    intent: ToolIntent,
    action_vars: ActionVariables,
    category: ToolCategory,
    trust: float,
) -> ToolDecision:
    """Decide whether to execute, clarify, defer, or refuse.

    Deterministic and testable. Driven by action variables,
    tool category, and trust level.
    """
    # Refuse: destructive tool + low risk tolerance
    if category == ToolCategory.DESTRUCTIVE and action_vars.risk_tolerance < 0.4:
        return ToolDecision(
            decision="refuse",
            intent=intent,
            rationale=f"Risk tolerance too low ({action_vars.risk_tolerance:.2f}) for destructive action",
        )

    # Clarify: low autonomy bias → ask first
    if action_vars.autonomy_bias < 0.35:
        return ToolDecision(
            decision="clarify",
            intent=intent,
            rationale=f"Autonomy bias too low ({action_vars.autonomy_bias:.2f}), need confirmation",
        )

    # Clarify: write/destructive + high clarification threshold
    if category in (ToolCategory.WRITE, ToolCategory.DESTRUCTIVE):
        if action_vars.clarification_threshold > 0.65:
            return ToolDecision(
                decision="clarify",
                intent=intent,
                rationale=f"Clarification threshold high ({action_vars.clarification_threshold:.2f}) for {category.value} action",
            )

    # Defer: extremely low urgency (too tired / no drive)
    if action_vars.action_urgency < 0.15:
        return ToolDecision(
            decision="defer",
            intent=intent,
            rationale=f"Action urgency too low ({action_vars.action_urgency:.2f}), deferring",
        )

    # Execute
    return ToolDecision(
        decision="execute",
        intent=intent,
        rationale="Action approved",
    )


# ---------------------------------------------------------------------------
# Summarize tool results for the generator (never raw output)
# ---------------------------------------------------------------------------

_MAX_SUMMARY_LEN = 500


def _summarize_for_generator(observations: list[ToolObservation], results: list[ToolResult]) -> str:
    """Build a concise summary of tool execution for the generator prompt.

    The generator should not see raw command output or large search pages.
    """
    if not observations:
        return ""

    parts: list[str] = []
    for obs, res in zip(observations, results):
        if res.success:
            output_preview = res.output[:_MAX_SUMMARY_LEN]
            if len(res.output) > _MAX_SUMMARY_LEN:
                output_preview += f"... ({len(res.output)} chars total)"
            parts.append(f"[{res.tool_name}] Success: {output_preview}")
        else:
            parts.append(f"[{res.tool_name}] Failed: {res.error}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main tool loop
# ---------------------------------------------------------------------------

def run_tool_loop(
    user_message: str,
    state: ModulatorState,
    person: PersonProfile | None,
    defense_active: bool,
    executor: ToolExecutor,
    engine: Any,  # EmotionalEngine — avoid circular import
    max_executions: int = DEFAULT_MAX_EXECUTIONS,
    hard_cap: int = HARD_CAP_EXECUTIONS,
) -> ToolLoopResult:
    """Run the cognitive tool loop.

    Called from the pipeline after inner dialogue, before defense mechanisms.
    Returns trace, action variables, and a generator-ready summary.
    """
    trust = person.trust if person else 0.5

    # 1. Derive action variables
    action_vars = derive_action_variables(state, trust=trust, defense_active=defense_active)

    # 2. Detect tool intent
    available = set(executor._registry.names())
    intent = detect_tool_intent(user_message, available)

    # No tool needed → empty trace
    if intent is None:
        return ToolLoopResult(
            trace=ToolTrace(),
            action_variables=action_vars,
            tool_context_summary="",
        )

    # Populate intent with derived action variables
    intent.urgency = action_vars.action_urgency
    intent.risk_tolerance = action_vars.risk_tolerance
    intent.autonomy_bias = action_vars.autonomy_bias
    intent.clarification_threshold = action_vars.clarification_threshold
    intent.persistence_drive = action_vars.persistence_drive

    # 3. Action arbiter
    capability = executor._registry.get(intent.tool_name)
    category = capability.category if capability else ToolCategory.READ_ONLY

    decision = make_tool_decision(intent, action_vars, category, trust)

    proposed_intents: list[ToolIntent] = [intent]
    executed_results: list[ToolResult] = []
    observations: list[ToolObservation] = []
    loop_count = 0

    if decision.decision != "execute":
        # Not executing — return trace with decision only
        return ToolLoopResult(
            trace=ToolTrace(
                proposed_intents=proposed_intents,
                final_decision=decision,
                loop_count=0,
            ),
            action_variables=action_vars,
            tool_context_summary="",
        )

    # 4. Execute + appraise loop (bounded)
    current_intent = intent
    while loop_count < min(max_executions, hard_cap):
        loop_count += 1

        # Execute
        result = executor.execute(current_intent.tool_name, current_intent.arguments)
        executed_results.append(result)

        # Appraise
        cap = executor._registry.get(current_intent.tool_name)
        cat = cap.category if cap else ToolCategory.READ_ONLY
        observation = appraise_tool_result(result, cat)
        observations.append(observation)

        # Apply emotional deltas to engine
        _apply_emotional_deltas(engine, observation)

        # Check if we should continue
        if not observation.continue_tool_loop:
            break

    # 5. Build summary for generator
    summary = _summarize_for_generator(observations, executed_results)

    return ToolLoopResult(
        trace=ToolTrace(
            proposed_intents=proposed_intents,
            final_decision=decision,
            executed_results=executed_results,
            observations=observations,
            loop_count=loop_count,
        ),
        action_variables=action_vars,
        tool_context_summary=summary,
    )


def _apply_emotional_deltas(engine: Any, observation: ToolObservation) -> None:
    """Apply an observation's emotional deltas to the engine state."""
    state = engine.state
    for modulator, delta in observation.emotional_delta.items():
        if hasattr(state, modulator):
            current = getattr(state, modulator)
            new_val = max(0.0, min(1.0, current + delta))
            setattr(state, modulator, new_val)
