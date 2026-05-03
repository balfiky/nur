"""Bounded multi-step task planner — Nūr-native.

Converts compound user requests into short explicit plans (max 5 steps).
All detection and planning is deterministic heuristic — zero LLM calls.

Responsibilities:
  1. Detect multi-step intent from user message
  2. Decompose into ordered TaskSteps
  3. Execute steps sequentially through the tool executor
  4. Track plan state, handle failures based on persistence drive
  5. Detect follow-up queries ("continue", "what happened?")
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from core.types import (
    ActionVariables,
    ModulatorState,
    TaskPlan,
    TaskStatus,
    TaskStep,
    TaskTrace,
    ToolCategory,
    ToolObservation,
    ToolResult,
)
from core.tool_appraisal import appraise_tool_result
from nur_tools.executor import ToolExecutor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_PLAN_STEPS = 5
DEFAULT_MAX_STEPS_PER_TURN = 3  # how many steps to execute in one turn


# ---------------------------------------------------------------------------
# Multi-step intent detection (heuristic)
# ---------------------------------------------------------------------------

# Compound patterns: "do X and then Y", "first X, then Y", "X then Y"
_COMPOUND_CONNECTORS = re.compile(
    r"\b(?:and\s+then|then|after\s+that|next|finally|also|and\s+also)\b",
    re.IGNORECASE,
)

# Sequential markers
_SEQUENCE_MARKERS = re.compile(
    r"\b(?:first|1\)|step\s+1|second|2\)|step\s+2|third|3\))\b",
    re.IGNORECASE,
)

_HOST_INFO_MULTI_STEP_RE = re.compile(
    r"\buname\s+-a\b.*(?:&&|\band\b|\bthen\b).*"
    r"\bcat\s+/etc/hostname\b",
    re.IGNORECASE | re.DOTALL,
)

# Single-step tool patterns (reused from tool_loop for decomposition)
_STEP_PATTERNS: list[tuple[re.Pattern, str, str]] = [
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
    # System inspection
    (re.compile(r"\b(?:what(?:'s|\s+is)|show|check|get|tell(?:\s+me)?)\b.{0,80}\b(?:memory|ram)\b.{0,80}\b(?:usage|utili[sz]ation|used|free|available|total)\b", re.I), "system.memory_usage", "empty"),
    (re.compile(r"\b(?:how\s+much|what(?:'s|\s+is))\b.{0,80}\b(?:memory|ram)\b.{0,80}\b(?:left|available|used|free|total)\b", re.I), "system.memory_usage", "empty"),
    (re.compile(r"^\s*free(?:\s+-[a-z]+)?\s*$", re.I), "system.memory_usage", "empty"),
    # Shell
    (re.compile(r"\b(?:what(?:'s|\s+is)|show|check|get|tell(?:\s+me)?)\b.{0,80}\b(?:your|the)?\s*(?:host\s*name|hostname|machine\s+name|node\s+name|server\s+name)\b", re.I), "shell.run_command", "cmd_hostname"),
    (re.compile(r"\bname\s+of\s+the\s+machine\b.{0,80}\b(?:running|run)\b", re.I), "shell.run_command", "cmd_hostname"),
    (re.compile(r"^\s*hostname\s*$", re.I), "shell.run_command", "cmd_hostname"),
    (re.compile(r"\b(?:run|execute|issue|check|show)\s+(?:the\s+)?(?:command\s+)?(hostname|uname(?:\s+-a)?)\b", re.I), "shell.run_command", "cmd_capture"),
    (re.compile(r"^\s*(uname(?:\s+-a)?)\s*$", re.I), "shell.run_command", "cmd_capture"),
    (re.compile(r"\bcat\s+(/etc/hostname)\b", re.I), "shell.run_command", "cmd_cat_path"),
    (re.compile(r"\b(?:how\s+much|what(?:'s|\s+is)|show|check|get|tell(?:\s+me)?)\b.{0,80}\b(?:disk|drive|filesystem|storage)\b.{0,80}\b(?:space|usage|free|left|available)\b", re.I), "shell.run_command", "cmd_disk_root"),
    (re.compile(r"^\s*(df(?:\s+-h)?(?:\s+/)?)\s*$", re.I), "shell.run_command", "cmd_capture"),
    (re.compile(r"\brun\s+(?:the\s+)?(?:command\s+)?[`\"']([^`\"']+)[`\"']", re.I), "shell.run_command", "cmd"),
    (re.compile(r"\bexecute\s+[`\"']([^`\"']+)[`\"']", re.I), "shell.run_command", "cmd"),
    (
        re.compile(
            r"\b(?:run|execute|issue)\s+(?:the\s+)?(?:command\s+)?"
            r"([^\n`;&|]+)\s*$",
            re.I,
        ),
        "shell.run_command",
        "cmd_explicit",
    ),
    (re.compile(r"^\s*do\s+([^\n`;&|]+)\s*$", re.I), "shell.run_command", "cmd_explicit"),
    # Web
    (re.compile(r"\bsearch\s+(?:the\s+)?web\s+for\s+[\"']?([^\"']+?)[\"']?\s*$", re.I), "web.search", "query"),
    (re.compile(r"\b(?:latest|newest|recent)\b.{0,100}\b(?:news|updates?|release|version|price|prices|score|scores|results?)\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:news|updates?)\s+(?:about|on|for)\s+[\"']?(.+?)[\"']?\s*$", re.I), "web.search", "query_full"),
    (re.compile(r"\bwhat\s+happened\s+(?:with|to|about|on|in)\s+[\"']?.+?[\"']?\s+(?:today|recently|this\s+(?:week|month|year))\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:current|live|today'?s)\s+(?:price|weather|score|scores|results?|status|exchange\s+rate|stock\s+price)\s+(?:of|for|in)?\s*[\"']?.+?[\"']?\s*$", re.I), "web.search", "query_full"),
    (re.compile(r"\bwho\s+(?:is|are)\s+(?:the\s+)?(?:current|new|latest)\s+.+$", re.I), "web.search", "query_full"),
    (re.compile(r"\bwhat\s+(?:is|are)\s+(?:the\s+)?(?:current|latest|newest)\s+(?:version|release|price|weather|score|status|exchange\s+rate)\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:check|look\s*up|find)\b.{0,40}\b(?:latest|current|recent|today'?s)\b.{2,120}$", re.I), "web.search", "query_full"),
    (re.compile(r"\bfetch\s+(?:the\s+)?(?:url\s+|page\s+(?:at\s+)?)?(https?://\S+)", re.I), "web.fetch", "url"),
    # Browser
    (re.compile(r"\bbrowse\s+(?:to\s+)?(https?://\S+)", re.I), "browser.open_url", "url"),
    (re.compile(r"\bscreenshot\s+(?:of\s+)?(https?://\S+)", re.I), "browser.screenshot", "url"),
]


def _extract_step_args(
    match: re.Match, extractor: str,
) -> dict[str, Any] | None:
    """Extract structured arguments from a regex match."""
    groups = match.groups()
    if extractor == "cmd_hostname":
        return {"cmd": "hostname"}
    if extractor == "cmd_disk_root":
        return {"cmd": "df -h /"}
    if extractor == "empty":
        return {}
    if extractor == "query_full":
        return {"query": _clean_search_query(match.group(0))}
    if not groups:
        return None

    if extractor == "path":
        return {"path": groups[0]}
    elif extractor == "cmd":
        return {"cmd": groups[0]}
    elif extractor == "cmd_capture":
        return {"cmd": groups[0]}
    elif extractor == "cmd_explicit":
        cmd = _clean_shell_command(groups[0])
        return {"cmd": cmd} if cmd else None
    elif extractor == "cmd_cat_path":
        return {"cmd": f"cat {groups[0]}"}
    elif extractor == "query":
        return {"query": _clean_search_query(groups[0])}
    elif extractor == "url":
        return {"url": groups[0]}
    elif extractor == "pattern_path" and len(groups) >= 2:
        return {"pattern": groups[0], "path": groups[1]}
    elif extractor == "content_path" and len(groups) >= 2:
        return {"content": groups[0], "path": groups[1]}
    elif extractor == "path_content" and len(groups) >= 2:
        return {"path": groups[0], "content": groups[1]}
    return None


def _clean_search_query(text: str) -> str:
    """Normalize a user phrase into a stable web-search query."""
    query = re.sub(r"\s+", " ", text).strip()
    query = query.strip("`\"' ")
    query = query.rstrip("?.! ")
    return query


_NON_COMMAND_STARTS = {
    "a",
    "again",
    "an",
    "anything",
    "here",
    "it",
    "me",
    "my",
    "necessary",
    "needed",
    "now",
    "please",
    "required",
    "right",
    "something",
    "stuff",
    "that",
    "the",
    "there",
    "thing",
    "this",
    "us",
    "we",
    "you",
    "your",
}


def _clean_shell_command(text: str) -> str | None:
    """Normalize an explicitly requested shell command without inventing args."""
    cmd = re.sub(r"\s+", " ", text).strip(" `\"'")
    cmd = cmd.rstrip(".!")
    if not cmd:
        return None
    first = cmd.split()[0].lower()
    if first in _NON_COMMAND_STARTS or first.startswith("-"):
        return None
    if not re.match(r"^[A-Za-z0-9_./~+-][A-Za-z0-9_./~+@%=-]*$", first):
        return None
    return cmd


def _parse_single_step(
    text: str, available_tools: set[str],
) -> TaskStep | None:
    """Try to parse a single tool step from text fragment."""
    for pattern, tool_name, extractor in _STEP_PATTERNS:
        if tool_name not in available_tools:
            continue
        m = pattern.search(text)
        if m:
            args = _extract_step_args(m, extractor)
            if args is not None:
                return TaskStep(
                    id=f"step_{uuid.uuid4().hex[:8]}",
                    tool_name=tool_name,
                    arguments=args,
                    description=f"{tool_name}: {text.strip()[:80]}",
                )
    return None


def detect_multi_step_intent(
    user_message: str,
    available_tools: set[str],
) -> TaskPlan | None:
    """Detect if a user message implies a multi-step task.

    Returns a TaskPlan if compound intent is found with 2+ steps,
    None for single-step or conversational messages.
    """
    host_info_plan = _detect_host_info_plan(user_message, available_tools)
    if host_info_plan is not None:
        return host_info_plan

    # Must have compound connectors or sequence markers
    has_connectors = bool(_COMPOUND_CONNECTORS.search(user_message))
    has_sequence = bool(_SEQUENCE_MARKERS.search(user_message))

    if not has_connectors and not has_sequence:
        return None

    # Split on connectors to get candidate segments
    segments = _COMPOUND_CONNECTORS.split(user_message)
    # Also try splitting on sequence markers
    if len(segments) < 2:
        segments = _SEQUENCE_MARKERS.split(user_message)

    # Clean and filter segments
    segments = [s.strip() for s in segments if s.strip() and len(s.strip()) > 5]

    if len(segments) < 2:
        return None

    # Try to parse each segment as a tool step
    steps: list[TaskStep] = []
    for seg in segments[:MAX_PLAN_STEPS]:
        step = _parse_single_step(seg, available_tools)
        if step:
            steps.append(step)

    # Need at least 2 valid steps for a multi-step plan
    if len(steps) < 2:
        return None

    return TaskPlan(
        id=f"plan_{uuid.uuid4().hex[:8]}",
        goal=user_message[:200],
        steps=steps,
    )


def _detect_host_info_plan(
    user_message: str,
    available_tools: set[str],
) -> TaskPlan | None:
    """Detect common host-inspection compound shell requests."""
    if "shell.run_command" not in available_tools:
        return None
    if not _HOST_INFO_MULTI_STEP_RE.search(user_message):
        return None
    return TaskPlan(
        id=f"plan_{uuid.uuid4().hex[:8]}",
        goal=user_message[:200],
        steps=[
            TaskStep(
                id=f"step_{uuid.uuid4().hex[:8]}",
                tool_name="shell.run_command",
                arguments={"cmd": "uname -a"},
                description="shell.run_command: uname -a",
            ),
            TaskStep(
                id=f"step_{uuid.uuid4().hex[:8]}",
                tool_name="shell.run_command",
                arguments={"cmd": "cat /etc/hostname"},
                description="shell.run_command: cat /etc/hostname",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Follow-up detection
# ---------------------------------------------------------------------------

_CONTINUE_PATTERNS = [
    re.compile(r"\bcontinue\b", re.I),
    re.compile(r"\bgo\s+on\b", re.I),
    re.compile(r"\bkeep\s+going\b", re.I),
    re.compile(r"\bnext\s+step\b", re.I),
    re.compile(r"\bproceed\b", re.I),
]

_STATUS_PATTERNS = [
    re.compile(r"\bwhat\s+happened\b", re.I),
    re.compile(r"\bwhat(?:'s|\s+is)\s+the\s+status\b", re.I),
    re.compile(r"\bhow(?:'s|\s+is)\s+(?:the\s+)?(?:task|plan)\b", re.I),
    re.compile(r"\bprogress\b", re.I),
]


def is_continue_request(user_message: str) -> bool:
    """Check if the user wants to continue an active plan."""
    return any(p.search(user_message) for p in _CONTINUE_PATTERNS)


def is_status_request(user_message: str) -> bool:
    """Check if the user is asking about plan status."""
    return any(p.search(user_message) for p in _STATUS_PATTERNS)


def summarize_plan_status(plan: TaskPlan) -> str:
    """Build a human-readable status summary for a plan."""
    parts = [f"Plan: {plan.goal[:100]}"]
    parts.append(f"Status: {plan.status.value}")
    parts.append(f"Progress: {plan.steps_completed}/{len(plan.steps)} steps completed")

    if plan.steps_failed > 0:
        parts.append(f"Failures: {plan.steps_failed}")

    for step in plan.steps:
        marker = {
            TaskStatus.COMPLETED: "[done]",
            TaskStatus.FAILED: "[FAIL]",
            TaskStatus.IN_PROGRESS: "[....]",
            TaskStatus.BLOCKED: "[BLOCK]",
            TaskStatus.PENDING: "[    ]",
        }.get(step.status, "[    ]")
        parts.append(f"  {marker} {step.description}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------

def execute_plan(
    plan: TaskPlan,
    executor: ToolExecutor,
    action_vars: ActionVariables,
    engine: Any = None,
    max_steps_per_turn: int = DEFAULT_MAX_STEPS_PER_TURN,
) -> TaskTrace:
    """Execute pending steps of a plan sequentially.

    Bounded by max_steps_per_turn per invocation.
    Failure handling is driven by persistence_drive:
      - persistence_drive >= 0.5 → continue after failure
      - persistence_drive < 0.5 → block the plan on first failure

    Returns a TaskTrace with execution details.
    """
    trace = TaskTrace(plan=plan)
    plan.status = TaskStatus.IN_PROGRESS
    plan.persistence_drive = action_vars.persistence_drive

    steps_this_turn = 0

    while (
        plan.current_step_index < len(plan.steps)
        and steps_this_turn < max_steps_per_turn
    ):
        step = plan.steps[plan.current_step_index]

        if step.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            plan.current_step_index += 1
            continue

        # Execute step
        step.status = TaskStatus.IN_PROGRESS
        step.started_at = time.time()

        result = executor.execute(step.tool_name, step.arguments)
        step.result = result
        step.completed_at = time.time()

        # Appraise
        cap = executor._registry.get(step.tool_name)
        category = cap.category if cap else ToolCategory.READ_ONLY
        observation = appraise_tool_result(result, category)
        step.observation = observation

        # Apply emotional deltas to engine if provided
        if engine is not None:
            _apply_emotional_deltas(engine, observation)

        trace.steps_executed += 1
        trace.total_latency_ms += result.latency_ms
        steps_this_turn += 1

        if result.success:
            step.status = TaskStatus.COMPLETED
            trace.steps_succeeded += 1
        else:
            step.status = TaskStatus.FAILED
            trace.steps_failed += 1

            # Persistence-driven failure handling
            if action_vars.persistence_drive < 0.5:
                plan.status = TaskStatus.BLOCKED
                trace.plan_outcome = "blocked"
                return trace
            else:
                trace.continued_after_failure = True

        plan.current_step_index += 1

    # Determine final plan status
    if plan.current_step_index >= len(plan.steps):
        # All steps attempted
        if trace.steps_failed == 0:
            plan.status = TaskStatus.COMPLETED
            plan.completed_at = time.time()
            trace.plan_outcome = "completed"
        elif trace.steps_succeeded > 0:
            plan.status = TaskStatus.COMPLETED
            plan.completed_at = time.time()
            trace.plan_outcome = "partial"
        else:
            plan.status = TaskStatus.FAILED
            plan.completed_at = time.time()
            trace.plan_outcome = "failed"
    else:
        # More steps remain (hit per-turn limit)
        trace.plan_outcome = "partial"

    return trace


def _apply_emotional_deltas(engine: Any, observation: ToolObservation) -> None:
    """Apply an observation's emotional deltas to the engine state."""
    state = engine.state
    for modulator, delta in observation.emotional_delta.items():
        if hasattr(state, modulator):
            current = getattr(state, modulator)
            new_val = max(0.0, min(1.0, current + delta))
            setattr(state, modulator, new_val)


# Avoid circular import
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass
