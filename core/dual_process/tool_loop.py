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
from dataclasses import dataclass, field
from typing import Any

from core.action_variables import derive_action_variables
from core.character_vector import CharacterVector
from core.life_influence import (
    LifeInfluence,
    action_variable_deltas,
    apply_character_vector_to_action_variables,
    apply_life_influence_to_action_variables,
)
from core.task_planning import (
    detect_multi_step_intent,
    execute_plan,
    is_continue_request,
    is_status_request,
    summarize_plan_status,
)
from core.tool_appraisal import appraise_tool_result
from core.types import (
    ActionVariables,
    AgencyDecision,
    ModulatorState,
    PersonProfile,
    TaskPlan,
    TaskTrace,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    ToolTrace,
)
from nur_tools.executor import ToolExecutor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_EXECUTIONS = 4
HARD_CAP_EXECUTIONS = 6

# Per-autonomy budgets. high_risk gets generous headroom for chains like
# clone -> install -> build -> run -> verify; lower autonomy stays tight.
AUTONOMY_EXECUTION_BUDGETS = {
    "off":        (0, 0),
    "assisted":   (3, 5),
    "autonomous": (6, 10),
    "high_risk":  (16, 32),
}


def autonomy_execution_budget(autonomy_level: str) -> tuple[int, int]:
    """Return (default, hard_cap) tool-execution budget for an autonomy level."""
    return AUTONOMY_EXECUTION_BUDGETS.get(
        _normalize_autonomy_level(autonomy_level),
        (DEFAULT_MAX_EXECUTIONS, HARD_CAP_EXECUTIONS),
    )

# Arbiter decision thresholds (Section 11 of design spec)
REFUSE_RISK_TOLERANCE = 0.4       # destructive + risk below this → refuse
CLARIFY_AUTONOMY_BIAS = 0.30     # autonomy below this → clarify
CLARIFY_WRITE_THRESHOLD = 0.65   # write/destructive + clarification above this → clarify
DEFER_URGENCY_THRESHOLD = 0.15   # urgency below this → defer


# ---------------------------------------------------------------------------
# Tool loop result
# ---------------------------------------------------------------------------

@dataclass
class ToolLoopResult:
    """What the tool loop returns to the pipeline."""
    trace: ToolTrace
    action_variables: ActionVariables
    tool_context_summary: str  # summarized for the generator — not raw output
    life_influence_effects: dict[str, float] = field(default_factory=dict)
    capability_gaps: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Heuristic tool intent detection
# ---------------------------------------------------------------------------

# Patterns: (compiled_regex, tool_name, arg_extractor_name)
_TOOL_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Skill registry — generic self-capability import/enabling.
    (
        re.compile(
            r"\b(?:list|show)\s+(?:the\s+)?"
            r"(?:imported\s+|enabled\s+|runtime\s+)?skills?\b",
            re.I,
        ),
        "skills.list",
        "empty",
    ),
    (
        re.compile(
            r"\b(?:enable|activate)\s+(?:the\s+)?"
            r"(?:skill|capability|module|integration)\s+"
            r"([A-Za-z0-9][A-Za-z0-9._-]{0,79})\b",
            re.I,
        ),
        "skills.enable",
        "skill_id",
    ),
    (
        re.compile(
            r"\b(?:disable|deactivate)\s+(?:the\s+)?"
            r"(?:skill|capability|module|integration)\s+"
            r"([A-Za-z0-9][A-Za-z0-9._-]{0,79})\b",
            re.I,
        ),
        "skills.disable",
        "skill_id",
    ),
    (
        re.compile(
            r"\b(?:delete|remove)\s+(?:the\s+)?"
            r"(?:skill|capability|module|integration)\s+"
            r"([A-Za-z0-9][A-Za-z0-9._-]{0,79})\b",
            re.I,
        ),
        "skills.delete",
        "skill_id",
    ),
    (
        re.compile(
            r"\b(?:audit|inspect|check|show)\b.{0,80}"
            r"\b(?:audit\s+log|audit|problem|errors?|warnings?|status|"
            r"skill|capability|module|integration)\b",
            re.I | re.S,
        ),
        "skills.audit",
        "optional_skill_id",
    ),
    (
        re.compile(
            r"\bwhat(?:'s|\s+is)\s+(?:the\s+)?problem\b.{0,100}"
            r"\b(?:skill|audit|import|registry)\b",
            re.I | re.S,
        ),
        "skills.audit",
        "optional_skill_id",
    ),
    (
        re.compile(
            r"\b(?:create|make|add|build|import|install|register)\b"
            r".{0,120}\b(?:skill|capability|module|integration)\b",
            re.I | re.S,
        ),
        "skills.create_from_request",
        "skill_request",
    ),
    (
        re.compile(
            r"\b(?:make|save|turn)\b.{0,60}\b(?:this|it)\b.{0,60}"
            r"\b(?:permanent|persistent|persisted)\b.{0,60}"
            r"\b(?:skill|capability|module|integration)?\b",
            re.I | re.S,
        ),
        "skills.create_from_request",
        "skill_request",
    ),
    # Filesystem — specific path first
    (re.compile(r"\bread(?:ing)?\s+(?:the\s+)?file\s+(\S+)", re.I), "fs.read_file", "path"),
    (re.compile(r"\b(?:show|cat|display)\s+(?:the\s+)?(?:contents?\s+of\s+)?(\S+\.\w+)", re.I), "fs.read_file", "path"),
    (re.compile(r"\blist\s+(?:the\s+)?(?:files?\s+in\s+|dir(?:ectory)?\s+)(\S+)", re.I), "fs.list_dir", "path"),
    (re.compile(r"\b(?:ls)\s+(\S+)", re.I), "fs.list_dir", "path"),
    (re.compile(r"\bwhat(?:'s| is)\s+in\s+((?:/|~)\S*)", re.I), "fs.list_dir", "path"),
    # Filesystem — no explicit path (default to / or .)
    (re.compile(r"\blist\s+(?:the\s+)?(?:system\s+)?files?\b", re.I), "fs.list_dir", "default_root"),
    (re.compile(r"\b(?:show|display)\s+(?:me\s+)?(?:the\s+)?(?:system\s+)?files?\b", re.I), "fs.list_dir", "default_root"),
    (re.compile(r"\bwhat\s+files?\b.*\b(?:here|available|exist)\b", re.I), "fs.list_dir", "default_cwd"),
    # Search/glob
    (re.compile(r"\bsearch\s+(?:for\s+)?[\"']([^\"']+)[\"']\s+in\s+(\S+)", re.I), "fs.search_text", "pattern_path"),
    (re.compile(r"\bgrep\s+[\"']?(\S+)[\"']?\s+(\S+)", re.I), "fs.search_text", "pattern_path"),
    (re.compile(r"\bfind\s+files?\s+(?:matching\s+)?[\"']?([^\"'\s]+)[\"']?\s+in\s+(\S+)", re.I), "fs.glob_paths", "pattern_path"),
    # Write/delete
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
    # Web — specific patterns first
    (re.compile(r"\bfetch\s+(?:the\s+)?(?:url\s+|page\s+(?:at\s+)?)?(https?://\S+)", re.I), "web.fetch", "url"),
    (re.compile(r"\bextract\s+(?:the\s+)?text\s+(?:from\s+)?(https?://\S+)", re.I), "web.extract_text", "url"),
    (re.compile(r"\bget\s+(?:the\s+)?(?:readable\s+)?text\s+(?:from\s+|of\s+)?(https?://\S+)", re.I), "web.extract_text", "url"),
    # Web — search with various phrasings
    (re.compile(r"\bsearch\s+(?:the\s+)?(?:web|internet|online)\s+(?:for\s+)?[\"']?(.+?)[\"']?\s*$", re.I), "web.search", "query"),
    (re.compile(r"\bsearch\s+(?:for\s+)?[\"']?(.+?)[\"']?\s+(?:on(?:line|\s+the\s+(?:web|internet)))", re.I), "web.search", "query"),
    (re.compile(r"\blook\s*up\s+[\"']?(.+?)[\"']?\s*(?:online|on\s+the\s+(?:web|internet))?\s*$", re.I), "web.search", "query"),
    (re.compile(r"\b(?:google|search\s+for)\s+[\"']?(.+?)[\"']?\s*$", re.I), "web.search", "query"),
    (re.compile(r"\bbrowse\s+(?:the\s+)?(?:web|internet)\s+(?:for\s+)?[\"']?(.+?)[\"']?\s*$", re.I), "web.search", "query"),
    (re.compile(r"\bfind\s+(?:me\s+)?(?:info(?:rmation)?|(?:some(?:thing)?)\s+)?\s*(?:about|on)\s+[\"']?(.+?)[\"']?\s+(?:online|on\s+the\s+(?:web|internet))", re.I), "web.search", "query"),
    (re.compile(r"\b(?:find|give|show|list|tell)\b.{0,140}\b(?:released|published|announced|launched|updated)\s+(?:after|since|in|during|this)\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:top|best)\b.{0,140}\b(?:released|published|announced|launched|updated|after|since|20[2-9]\d)\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:latest|newest|recent)\b.{0,100}\b(?:news|updates?|release|version|price|prices|score|scores|results?)\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:news|updates?)\s+(?:about|on|for)\s+[\"']?(.+?)[\"']?\s*$", re.I), "web.search", "query_full"),
    (re.compile(r"\bwhat\s+happened\s+(?:with|to|about|on|in)\s+[\"']?.+?[\"']?\s+(?:today|recently|this\s+(?:week|month|year))\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:current|live|today'?s)\s+(?:price|weather|score|scores|results?|status|exchange\s+rate|stock\s+price)\s+(?:of|for|in)?\s*[\"']?.+?[\"']?\s*$", re.I), "web.search", "query_full"),
    (re.compile(r"\bwho\s+(?:is|are)\s+(?:the\s+)?(?:current|new|latest)\s+.+$", re.I), "web.search", "query_full"),
    (re.compile(r"\bwhat\s+(?:is|are)\s+(?:the\s+)?(?:current|latest|newest)\s+(?:version|release|price|weather|score|status|exchange\s+rate)\b.*$", re.I), "web.search", "query_full"),
    (re.compile(r"\b(?:check|look\s*up|find)\b.{0,40}\b(?:latest|current|recent|today'?s)\b.{2,120}$", re.I), "web.search", "query_full"),
    # Browser
    (re.compile(r"\bopen\s+(?:the\s+)?(?:url\s+|page\s+(?:at\s+)?)?(https?://\S+)\s+in\s+(?:the\s+)?browser", re.I), "browser.open_url", "url"),
    (re.compile(r"\bbrowse\s+(?:to\s+)?(https?://\S+)", re.I), "browser.open_url", "url"),
    (re.compile(r"\bget\s+(?:the\s+)?page\s+text\s+(?:from\s+|of\s+)?(https?://\S+)", re.I), "browser.get_page_text", "url"),
    (re.compile(r"\bscreenshot\s+(?:of\s+)?(https?://\S+)", re.I), "browser.screenshot", "url"),
    (re.compile(r"\btake\s+(?:a\s+)?screenshot(?:\s+of\s+(https?://\S+))?", re.I), "browser.screenshot", "url"),
    # Calendar
    (re.compile(r"\b(?:list|show|what(?:'s| are)?)\s+(?:my\s+)?(?:calendar\s+)?events?\s+(?:for\s+|on\s+)(\S+)", re.I), "calendar.list_events", "date"),
    (re.compile(r"\b(?:check|view)\s+(?:my\s+)?calendar\s+(?:for\s+|on\s+)(\S+)", re.I), "calendar.list_events", "date"),
    (re.compile(r"\bcreate\s+(?:a\s+)?(?:calendar\s+)?event\s+[\"']([^\"']+)[\"']\s+(?:on|at|from)\s+(\S+)", re.I), "calendar.create_event", "title_date"),
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


def detect_capability_gap(user_message: str, available_tools: set[str]) -> dict[str, Any] | None:
    """Detect a likely capability gap when no current tool can satisfy the request."""
    text = (user_message or "").lower()
    if "pdf" in text and not any("pdf" in name.lower() for name in available_tools):
        return {
            "id": "gap:pdf_reader",
            "gap_type": "pdf_reader",
            "recent_recurrence": 1,
            "frustration_intensity": 0.4,
        }
    if re.search(r"\b(transcribe|speech[- ]to[- ]text|audio)\b", text) and not any(
        "transcribe" in name.lower() or "speech" in name.lower() or "audio" in name.lower()
        for name in available_tools
    ):
        return {
            "id": "gap:speech_to_text",
            "gap_type": "speech_to_text",
            "recent_recurrence": 1,
            "frustration_intensity": 0.35,
        }

    gap_patterns = (
        (
            "image_generation",
            r"\b(?:generate|create|draw|edit)\b.{0,60}"
            r"\b(?:image|picture|photo|avatar|logo)\b",
        ),
        (
            "video_processing",
            r"\b(?:video|clip|movie)\b.{0,80}"
            r"\b(?:summari[sz]e|transcribe|edit|analy[sz]e)\b",
        ),
        (
            "spreadsheet",
            r"(?:\b(?:xlsx|spreadsheet|excel|csv)\b.{0,80}"
            r"\b(?:analy[sz]e|chart|pivot|clean|merge)\b|"
            r"\b(?:analy[sz]e|chart|pivot|clean|merge)\b.{0,80}"
            r"\b(?:xlsx|spreadsheet|excel|csv)\b)",
        ),
        (
            "database",
            r"\b(?:query|inspect|migrate|connect)\b.{0,80}"
            r"\b(?:database|postgres|mysql|sqlite|sql)\b",
        ),
        (
            "code_execution",
            r"\b(?:run|execute|debug|test)\b.{0,80}"
            r"\b(?:code|script|notebook|program)\b",
        ),
    )
    available_blob = " ".join(name.lower() for name in available_tools)
    for gap_type, pattern in gap_patterns:
        if re.search(pattern, text) and not all(
            part in available_blob for part in gap_type.split("_")
        ):
            return {
                "id": f"gap:{gap_type}",
                "gap_type": gap_type,
                "recent_recurrence": 1,
                "frustration_intensity": 0.3,
            }
    return None


def _extract_args(
    match: re.Match, tool_name: str, extractor: str,
) -> dict[str, Any] | None:
    """Extract structured arguments from a regex match."""
    groups = match.groups()

    if extractor == "default_root":
        return {"path": "/"}
    elif extractor == "empty":
        return {}
    elif extractor == "skill_request":
        request = re.sub(r"\s+", " ", match.string or "").strip()
        return {
            "request": request,
            "name_hint": _extract_skill_name_hint(request),
            "source_url": _extract_source_url(request),
            "enable": True,
        } if request else None
    elif extractor == "optional_skill_id":
        return {"skill_id": _extract_skill_id_hint(match.string or "")}
    elif extractor == "default_cwd":
        return {"path": "."}
    elif extractor == "cmd_hostname":
        return {"cmd": "hostname"}
    elif extractor == "cmd_disk_root":
        return {"cmd": "df -h /"}
    elif extractor == "query_full":
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
    elif extractor == "date":
        return {"date": groups[0]}
    elif extractor == "title_date" and len(groups) >= 2:
        return {"title": groups[0], "start": groups[1], "end": groups[1]}
    elif extractor == "skill_id":
        return {"skill_id": groups[0]}
    return None


def _clean_search_query(text: str) -> str:
    """Normalize a user phrase into a stable web-search query."""
    query = re.sub(r"\s+", " ", text).strip()
    query = query.strip("`\"' ")
    query = query.rstrip("?.! ")
    return query


def _extract_source_url(text: str) -> str:
    match = re.search(r"https?://\S+", text or "", flags=re.I)
    return match.group(0).rstrip(").,;") if match else ""


def _extract_skill_name_hint(text: str) -> str:
    match = re.search(
        r"\b(?:called|named)\s+([A-Za-z0-9][A-Za-z0-9 _.-]{1,80})",
        text or "",
        flags=re.I,
    )
    return match.group(1).strip() if match else ""


def _extract_skill_id_hint(text: str) -> str:
    match = re.search(
        r"\b(?:skill|capability|module|integration)\s+"
        r"([A-Za-z0-9][A-Za-z0-9._-]{0,79})\b",
        text or "",
        flags=re.I,
    )
    return match.group(1).strip() if match else ""


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


# ---------------------------------------------------------------------------
# Action arbiter — cognitive decision layer
# ---------------------------------------------------------------------------

def make_tool_decision(
    intent: ToolIntent,
    action_vars: ActionVariables,
    category: ToolCategory,
    trust: float,
    agency_decision: AgencyDecision | None = None,
    autonomy_level: str = "autonomous",
) -> ToolDecision:
    """Decide whether to execute, clarify, defer, or refuse.

    Deterministic and testable. Driven by action variables,
    tool category, and trust level.
    """
    normalized_autonomy = _normalize_autonomy_level(autonomy_level)
    agency_action = agency_decision.action if agency_decision else "comply"

    if normalized_autonomy == "off":
        return ToolDecision(
            decision="refuse",
            intent=intent,
            rationale="Autonomy mode is off; tools are not allowed",
        )

    if agency_action in {"refuse", "demand_repair"}:
        return ToolDecision(
            decision="refuse",
            intent=intent,
            rationale=(
                f"Agency stance is {agency_action}: "
                f"{agency_decision.tool_instruction if agency_decision else ''}"
            ).strip(),
        )

    if agency_action == "disengage" and category != ToolCategory.READ_ONLY:
        return ToolDecision(
            decision="refuse",
            intent=intent,
            rationale=(
                "Agency stance is disengage; low energy allows simple read-only "
                "inspection but blocks side-effecting work"
            ),
        )

    if normalized_autonomy == "assisted" and category in (
        ToolCategory.WRITE,
        ToolCategory.DESTRUCTIVE,
        ToolCategory.EXTERNAL_ACTION,
    ):
        return ToolDecision(
            decision="clarify",
            intent=intent,
            rationale=f"Assisted autonomy requires confirmation for {category.value} action",
        )

    if agency_action == "slow_down" and category in (
        ToolCategory.WRITE,
        ToolCategory.DESTRUCTIVE,
        ToolCategory.EXTERNAL_ACTION,
    ):
        return ToolDecision(
            decision="clarify",
            intent=intent,
            rationale="Agency stance is slow_down; risky action needs confirmation",
        )

    if agency_action == "resist" and category in (
        ToolCategory.WRITE,
        ToolCategory.DESTRUCTIVE,
    ):
        return ToolDecision(
            decision="clarify",
            intent=intent,
            rationale="Agency stance is resist; write/destructive action needs a clearer request",
        )

    if normalized_autonomy == "high_risk":
        return ToolDecision(
            decision="execute",
            intent=intent,
            rationale="High-risk autonomy approved within configured tool scope",
        )

    # Refuse: destructive tool + low risk tolerance
    if category == ToolCategory.DESTRUCTIVE and action_vars.risk_tolerance < REFUSE_RISK_TOLERANCE:
        return ToolDecision(
            decision="refuse",
            intent=intent,
            rationale=f"Risk tolerance too low ({action_vars.risk_tolerance:.2f}) for destructive action",
        )

    # Clarify: low autonomy bias → ask first
    if action_vars.autonomy_bias < CLARIFY_AUTONOMY_BIAS:
        return ToolDecision(
            decision="clarify",
            intent=intent,
            rationale=f"Autonomy bias too low ({action_vars.autonomy_bias:.2f}), need confirmation",
        )

    # Clarify: write/destructive + high clarification threshold
    if category in (ToolCategory.WRITE, ToolCategory.DESTRUCTIVE):
        if action_vars.clarification_threshold > CLARIFY_WRITE_THRESHOLD:
            return ToolDecision(
                decision="clarify",
                intent=intent,
                rationale=f"Clarification threshold high ({action_vars.clarification_threshold:.2f}) for {category.value} action",
            )

    # Defer: extremely low urgency (too tired / no drive)
    if action_vars.action_urgency < DEFER_URGENCY_THRESHOLD and category != ToolCategory.READ_ONLY:
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


def _normalize_autonomy_level(value: str) -> str:
    return value if value in {"off", "assisted", "autonomous", "high_risk"} else "autonomous"


# ---------------------------------------------------------------------------
# Summarize tool results for the generator (bounded output only)
# ---------------------------------------------------------------------------

def _summarize_for_generator(observations: list[ToolObservation], results: list[ToolResult]) -> str:
    """Build a concise summary of tool execution for the generator prompt.

    The generator sees bounded shell output so it can report observed command
    results instead of guessing. Other tool outputs stay summarized.
    """
    if not observations:
        return ""

    parts: list[str] = []
    for obs, res in zip(observations, results):
        if res.success:
            details: list[str] = [obs.summary]
            if res.side_effect_summary and res.side_effect_summary != "none":
                details.append(res.side_effect_summary)
            metadata = _format_result_metadata(res.metadata)
            if metadata:
                details.append(metadata)
            output = _format_result_output(res)
            if output:
                details.append(output)
            parts.append(f"[{res.tool_name}] " + " | ".join(details))
        else:
            details = [f"Failed: {res.error}"]
            if res.side_effect_summary and res.side_effect_summary != "none":
                details.append(res.side_effect_summary)
            metadata = _format_result_metadata(res.metadata)
            if metadata:
                details.append(metadata)
            output = _format_result_output(res)
            if output:
                details.append(output)
            parts.append(f"[{res.tool_name}] " + " | ".join(details))

    return "\n".join(parts)


def _format_result_output(result: ToolResult, limit: int = 1200) -> str:
    if result.tool_name not in {
        "shell.run_command",
        "system.hostname",
        "system.uname",
        "system.disk_usage",
        "system.memory_usage",
        "system.installed_packages",
        "web.search",
        "web.fetch",
        "web.extract_text",
        "skills.audit",
        "skills.create_from_request",
        "skills.import_markdown",
    } or not result.output:
        return ""
    text = result.output.strip()
    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return f"output={text!r}"


# ---------------------------------------------------------------------------
# Main tool loop
# ---------------------------------------------------------------------------

def _with_character_state(
    action_vars: ActionVariables,
    life_influence: LifeInfluence | None,
    character_vector: CharacterVector | None,
    category: ToolCategory,
) -> ActionVariables:
    if character_vector is not None:
        return apply_character_vector_to_action_variables(
            action_vars,
            character_vector,
            read_only_action=category == ToolCategory.READ_ONLY,
        )
    if life_influence is None or life_influence.is_neutral:
        return action_vars
    return apply_life_influence_to_action_variables(
        action_vars,
        life_influence,
        read_only_action=category == ToolCategory.READ_ONLY,
    )


def _action_effects(before: ActionVariables, after: ActionVariables) -> dict[str, float]:
    return {
        key: value for key, value in action_variable_deltas(before, after).items()
        if value != 0.0
    }


def run_tool_loop(
    user_message: str,
    state: ModulatorState,
    person: PersonProfile | None,
    defense_active: bool,
    executor: ToolExecutor,
    engine: Any,  # EmotionalEngine — avoid circular import
    max_executions: int | None = None,
    hard_cap: int | None = None,
    active_plan: TaskPlan | None = None,
    agency_decision: AgencyDecision | None = None,
    autonomy_level: str = "autonomous",
    life_influence: LifeInfluence | None = None,
    character_vector: CharacterVector | None = None,
) -> ToolLoopResult:
    """Run the cognitive tool loop.

    Called from the pipeline after inner dialogue, before defense mechanisms.
    Returns trace, action variables, and a generator-ready summary.

    When ``max_executions`` / ``hard_cap`` are not given, the budget is
    derived from ``autonomy_level`` via :func:`autonomy_execution_budget`.

    If active_plan is provided and the user says "continue"/"next step",
    resumes that plan instead of detecting new intent.
    """
    if max_executions is None or hard_cap is None:
        budget_default, budget_cap = autonomy_execution_budget(autonomy_level)
        if max_executions is None:
            max_executions = budget_default
        if hard_cap is None:
            hard_cap = budget_cap
    trust = person.trust if person else 0.5

    # 1. Derive action variables
    action_vars = derive_action_variables(state, trust=trust, defense_active=defense_active)

    available = set(executor._registry.names())

    # 1a. Check for plan status request
    if active_plan and not active_plan.is_terminal and is_status_request(user_message):
        status_summary = summarize_plan_status(active_plan)
        return ToolLoopResult(
            trace=ToolTrace(),
            action_variables=action_vars,
            tool_context_summary=status_summary,
        )

    # 1b. Check for plan continuation
    if active_plan and not active_plan.is_terminal and is_continue_request(user_message):
        task_trace = execute_plan(
            active_plan, executor, action_vars, engine=engine,
        )
        # Collect results and observations from plan steps
        executed_results, observations = _collect_plan_results(active_plan)
        summary = _summarize_plan_for_generator(active_plan, task_trace)
        return ToolLoopResult(
            trace=ToolTrace(
                executed_results=executed_results,
                observations=observations,
                loop_count=task_trace.steps_executed,
                task_trace=task_trace,
            ),
            action_variables=action_vars,
            tool_context_summary=summary,
        )

    # 2. Detect multi-step intent first
    plan = detect_multi_step_intent(user_message, available)
    if plan is not None:
        # Arbiter check: use first step's tool for category check
        first_step = plan.steps[0]
        cap = executor._registry.get(first_step.tool_name)
        first_category = cap.category if cap else ToolCategory.READ_ONLY
        plan_action_vars = _with_character_state(
            action_vars,
            life_influence,
            character_vector,
            first_category,
        )
        life_effects = _action_effects(action_vars, plan_action_vars)
        # Build a synthetic intent for arbiter
        synthetic_intent = ToolIntent(
            tool_name=first_step.tool_name,
            arguments=first_step.arguments,
            reason=f"Multi-step plan: {plan.goal[:80]}",
            expected_outcome=f"Execute {len(plan.steps)}-step plan",
            urgency=plan_action_vars.action_urgency,
            risk_tolerance=plan_action_vars.risk_tolerance,
            autonomy_bias=plan_action_vars.autonomy_bias,
            clarification_threshold=plan_action_vars.clarification_threshold,
            persistence_drive=plan_action_vars.persistence_drive,
        )
        decision = make_tool_decision(
            synthetic_intent,
            plan_action_vars,
            first_category,
            trust,
            agency_decision=agency_decision,
            autonomy_level=autonomy_level,
        )

        if decision.decision != "execute":
            return ToolLoopResult(
                trace=ToolTrace(
                    proposed_intents=[synthetic_intent],
                    final_decision=decision,
                    loop_count=0,
                    task_trace=TaskTrace(plan=plan, plan_outcome=""),
                ),
                action_variables=plan_action_vars,
                tool_context_summary="",
                life_influence_effects=life_effects,
            )

        # Execute the plan
        task_trace = execute_plan(
            plan, executor, plan_action_vars, engine=engine,
        )
        executed_results, observations = _collect_plan_results(plan)
        summary = _summarize_plan_for_generator(plan, task_trace)

        return ToolLoopResult(
            trace=ToolTrace(
                proposed_intents=[synthetic_intent],
                final_decision=decision,
                executed_results=executed_results,
                observations=observations,
                loop_count=task_trace.steps_executed,
                task_trace=task_trace,
            ),
            action_variables=plan_action_vars,
            tool_context_summary=summary,
            life_influence_effects=life_effects,
        )

    # 3. Single-step: detect tool intent
    intent = detect_tool_intent(user_message, available)

    # No tool needed → empty trace
    if intent is None:
        gap = detect_capability_gap(user_message, available)
        return ToolLoopResult(
            trace=ToolTrace(),
            action_variables=action_vars,
            tool_context_summary="",
            capability_gaps=[gap] if gap else [],
        )

    # Populate intent with derived action variables
    intent.urgency = action_vars.action_urgency
    intent.risk_tolerance = action_vars.risk_tolerance
    intent.autonomy_bias = action_vars.autonomy_bias
    intent.clarification_threshold = action_vars.clarification_threshold
    intent.persistence_drive = action_vars.persistence_drive

    # 4. Action arbiter
    capability = executor._registry.get(intent.tool_name)
    category = capability.category if capability else ToolCategory.READ_ONLY
    action_vars = _with_character_state(
        action_vars,
        life_influence,
        character_vector,
        category,
    )
    life_effects = _action_effects(
        derive_action_variables(state, trust=trust, defense_active=defense_active),
        action_vars,
    )
    intent.urgency = action_vars.action_urgency
    intent.risk_tolerance = action_vars.risk_tolerance
    intent.autonomy_bias = action_vars.autonomy_bias
    intent.clarification_threshold = action_vars.clarification_threshold
    intent.persistence_drive = action_vars.persistence_drive

    decision = make_tool_decision(
        intent,
        action_vars,
        category,
        trust,
        agency_decision=agency_decision,
        autonomy_level=autonomy_level,
    )

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
            life_influence_effects=life_effects,
        )

    # 5. Execute + appraise loop (bounded)
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

    # 6. Build summary for generator
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
        life_influence_effects=life_effects,
    )


def _collect_plan_results(
    plan: TaskPlan,
) -> tuple[list[ToolResult], list[ToolObservation]]:
    """Collect executed results and observations from plan steps."""
    results: list[ToolResult] = []
    observations: list[ToolObservation] = []
    for step in plan.steps:
        if step.result is not None:
            results.append(step.result)
        if step.observation is not None:
            observations.append(step.observation)
    return results, observations


def _summarize_plan_for_generator(
    plan: TaskPlan, task_trace: TaskTrace,
) -> str:
    """Build a concise summary of multi-step plan execution for the generator."""
    parts: list[str] = []
    parts.append(f"[Plan: {plan.goal[:100]}]")
    parts.append(f"Outcome: {task_trace.plan_outcome} "
                 f"({task_trace.steps_succeeded}/{task_trace.steps_executed} succeeded)")

    for step in plan.steps:
        if step.result is not None:
            if step.result.success:
                details = []
                if step.observation is not None:
                    details.append(step.observation.summary)
                if (
                    step.result.side_effect_summary
                    and step.result.side_effect_summary != "none"
                ):
                    details.append(step.result.side_effect_summary)
                metadata = _format_result_metadata(step.result.metadata)
                if metadata:
                    details.append(metadata)
                output = _format_result_output(step.result)
                if output:
                    details.append(output)
                summary = " | ".join(details) if details else "success"
                parts.append(f"  [{step.tool_name}] OK: {summary}")
            else:
                parts.append(f"  [{step.tool_name}] FAIL: {step.result.error}")

    return "\n".join(parts)


def _format_result_metadata(metadata: dict[str, Any]) -> str:
    """Return only compact, low-risk metadata hints for generation."""
    if not metadata:
        return ""
    hints: list[str] = []
    for key in (
        "count",
        "match_count",
        "result_count",
        "bytes_written",
        "exit_code",
        "skill_id",
        "status",
        "enabled",
        "error_count",
        "warning_count",
        "truncated",
    ):
        if key in metadata:
            hints.append(f"{key}={metadata[key]}")
    return ", ".join(hints)


def _apply_emotional_deltas(engine: Any, observation: ToolObservation) -> None:
    """Apply an observation's emotional deltas to the engine state."""
    state = engine.state
    for modulator, delta in observation.emotional_delta.items():
        if hasattr(state, modulator):
            current = getattr(state, modulator)
            new_val = max(0.0, min(1.0, current + delta))
            setattr(state, modulator, new_val)
