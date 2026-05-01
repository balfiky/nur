"""Ground response claims about external actions in actual tool evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class ActionClaim:
    """A response claim that Nūr performed an external action."""

    text: str
    categories: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class GroundingIssue:
    """A response grounding problem suitable for debug/self-check output."""

    code: str
    message: str
    claim: str = ""
    required_categories: tuple[str, ...] = ()
    evidence_categories: tuple[str, ...] = ()


_ACTION_VERBS_RE = re.compile(
    r"\b("
    r"read(?:ing)?|wrote|writing|write|created?|creating|saved?|saving|"
    r"cloned?|cloning|fetched?|fetching|downloaded?|downloading|installed?|installing|"
    r"ran|running|executed?|executing|checked?|checking|opened?|opening|"
    r"searched?|searching|listed?|listing|inspected?|inspecting|loaded?|loading|"
    r"called?|calling|integrated?|integrating|registered|registering|added|adding"
    r")\b",
    re.IGNORECASE,
)

_EXTERNAL_OBJECT_RE = re.compile(
    r"\b("
    r"repo(?:sitory)?|checkout|file|folder|directory|script|logs?|output|"
    r"workspace|environment|shell|terminal|command|process|package|dependency|"
    r"url|web(?:site)?|page|api|endpoint|server|database|registry|skill|"
    r"capability|capabilities|module|integration|toolset|skillset"
    r")\b",
    re.IGNORECASE,
)

_FIRST_PERSON_ACTION_RE = re.compile(
    r"\b(?:i(?:'m| am| have|\'ve)?|now)\b.{0,80}"
    r"\b(?:read(?:ing)?|wrote|writing|created?|creating|saved?|saving|"
    r"cloned?|cloning|fetched?|fetching|downloaded?|downloading|installed?|installing|"
    r"ran|running|executed?|executing|checked?|checking|opened?|opening|"
    r"searched?|searching|listed?|listing|inspected?|inspecting|loaded?|loading|"
    r"called?|calling|integrated?|integrating|registered|registering|added|adding)"
    r"\b.{0,100}",
    re.IGNORECASE | re.DOTALL,
)

_STATUS_CLAIM_RE = re.compile(
    r"\b(?:tool|shell|terminal|command|process|install|download|clone|fetch|write|read)"
    r"\b.{0,60}\b(?:failed|succeeded|completed|returned|produced|output|logs?)\b",
    re.IGNORECASE | re.DOTALL,
)

_REGISTRY_STATE_CLAIM_RE = re.compile(
    r"\b(?:created?|creating|imported?|importing|enabled?|enabling|"
    r"activated?|activating|installed?|installing|integrated?|integrating|"
    r"registered|registering|added|adding|made\s+permanent)\b"
    r".{0,100}\b(?:skill|capability|capabilities|module|integration|"
    r"registry|toolset|skillset)\b"
    r"|"
    r"\b(?:skill|capability|capabilities|module|integration|toolset|skillset)"
    r"\b.{0,100}\b(?:created?|imported?|enabled?|activated?|installed?|"
    r"integrated?|registered|added|permanent|persistent|persisted)\b"
    r"|"
    r"\b(?:now|already)\b.{0,80}\b(?:part\s+of|available\s+in|added\s+to)\b"
    r".{0,80}\b(?:context|runtime|registry|configuration|toolset|skillset|"
    r"capability|capabilities)\b",
    re.IGNORECASE | re.DOTALL,
)


def verify_response_grounding(
    response: str,
    *,
    tool_trace: Any | None,
) -> list[GroundingIssue]:
    """Return issues for external-action claims not backed by tool evidence.

    This is presentation/output verification. It does not infer user intent and
    it does not special-case any product, repo, provider, or prompt. The rule is
    simple: claims that Nūr performed external actions must be backed by a
    matching category of executed tool result.
    """
    claims = extract_action_claims(response)
    if not claims:
        return []

    evidence = _tool_evidence_categories(tool_trace)
    issues: list[GroundingIssue] = []
    for claim in claims:
        missing = _missing_categories(claim.categories, evidence)
        if not missing:
            continue
        issues.append(
            GroundingIssue(
                code="unverified_external_action_claim",
                message=(
                    "Unverified external-action claim. Do not say that an "
                    "external action was performed unless matching Tool "
                    "Execution Results confirm it."
                ),
                claim=claim.text,
                required_categories=tuple(sorted(claim.categories)),
                evidence_categories=tuple(sorted(evidence)),
            )
        )
    return issues


def extract_action_claims(response: str) -> list[ActionClaim]:
    """Extract generic first-person or status claims about external actions."""
    if not response:
        return []
    claims: list[ActionClaim] = []
    seen: set[str] = set()
    for pattern in (_FIRST_PERSON_ACTION_RE, _STATUS_CLAIM_RE, _REGISTRY_STATE_CLAIM_RE):
        for match in pattern.finditer(response):
            text = _compact(match.group(0))
            if _seen_claim_text(text, seen) or not _EXTERNAL_OBJECT_RE.search(text):
                continue
            context = response[max(0, match.start() - 80):min(len(response), match.end() + 40)]
            if _is_negated_or_evidence_warning(context):
                continue
            categories = _claim_categories(text)
            if not categories:
                continue
            seen.add(text)
            claims.append(ActionClaim(text=text, categories=categories))
    return claims


def grounding_correction_response(
    issues: list[GroundingIssue] | None = None,
) -> str:
    """Return a generic correction when output overclaims external action."""
    if any(
        "registry_write" in issue.required_categories
        for issue in (issues or [])
    ):
        return (
            "I did not create, import, or enable a permanent skill in this turn. "
            "No skill-registry Tool Execution Result ran. To let me do that "
            "from chat, Agentic Tools must be enabled and the current session "
            "must be reloaded so the skill-registry tools are available."
        )
    return (
        "I did not perform that external action in this turn. There is no Tool "
        "Execution Result showing that I read files, cloned or fetched a "
        "repository, ran a command, installed a package, inspected logs/output, "
        "or wrote anything. I can draft instructions or a SKILL.md, but a "
        "permanent skill must be imported and enabled through Admin > Skills "
        "or an executed skill-registry tool."
    )


def _claim_categories(text: str) -> set[str]:
    lower = text.lower()
    categories: set[str] = set()
    if re.search(r"\b(read|reading|checked?|checking|opened?|opening|searched?|searching|listed?|listing|inspected?|inspecting|fetched?|fetching|loaded?|loading)\b", lower):
        categories.add("read")
    if re.search(r"\b(wrote|write|writing|created?|creating|saved?|saving|installed?|installing|downloaded?|downloading|cloned?|cloning)\b", lower):
        categories.add("write")
    if re.search(r"\b(ran|running|executed?|executing|shell|terminal|command|process|package|dependency)\b", lower):
        categories.add("execute")
    if re.search(r"\b(skill|capability|capabilities|module|integration|registry|toolset|skillset)\b", lower) and re.search(
        r"\b(created?|creating|imported?|importing|enabled?|enabling|"
        r"activated?|activating|installed?|installing|integrated?|integrating|"
        r"registered|registering|added|adding|made\s+permanent|permanent|"
        r"persistent|persisted|part\s+of|available\s+in)\b",
        lower,
    ):
        categories.add("registry_write")
    return categories


def _tool_evidence_categories(tool_trace: Any | None) -> set[str]:
    executed = list(getattr(tool_trace, "executed_results", []) or [])
    if not executed:
        return set()
    categories: set[str] = set()
    for result in executed:
        tool_name = str(getattr(result, "tool_name", "") or "").lower()
        if tool_name.startswith(("fs.read", "fs.list", "fs.search", "fs.glob")):
            categories.add("read")
        elif tool_name.startswith(("web.", "browser.", "system.")):
            categories.add("read")
        elif tool_name.startswith(("fs.write", "fs.delete")):
            categories.add("write")
        elif tool_name.startswith((
            "skills.create",
            "skills.import",
            "skills.enable",
            "skills.disable",
        )):
            categories.add("registry_write")
        elif tool_name.startswith(("skills.list", "skills.audit")):
            categories.add("read")
        elif tool_name.startswith("shell."):
            categories.update({"read", "write", "execute"})
        else:
            categories.add("tool")
    return categories


def _missing_categories(required: set[str], evidence: set[str]) -> set[str]:
    missing: set[str] = set()
    for category in required:
        if category == "read" and evidence.intersection({"read", "execute", "tool"}):
            continue
        if category == "write" and evidence.intersection({"write", "execute", "tool"}):
            continue
        if category == "execute" and evidence.intersection({"execute", "tool"}):
            continue
        if category == "registry_write" and evidence.intersection({"registry_write"}):
            continue
        missing.add(category)
    return missing


def _compact(text: str) -> str:
    return " ".join(str(text or "").split())


def _seen_claim_text(text: str, seen: set[str]) -> bool:
    normalized = text.lower()
    return any(
        normalized == prior.lower()
        or normalized in prior.lower()
        or prior.lower() in normalized
        for prior in seen
    )


def _is_negated_or_evidence_warning(text: str) -> bool:
    lower = text.lower()
    if "no tool execution result" in lower:
        return True
    if "without tool execution result" in lower:
        return True
    if _is_non_assistant_actor_statement(lower):
        return True
    if re.search(
        r"\b(?:must|should|needs?\s+to|has\s+to|can|could)\s+be\s+"
        r"(?:created|imported|enabled|activated|installed|integrated|"
        r"registered|added)\b",
        lower,
    ):
        return True
    if re.search(
        r"\bif\s+you\b.{0,60}\b(?:create|import|enable|activate|install|"
        r"integrate|register|add)\b",
        lower,
    ):
        return True
    if re.search(r"\b(?:i|we)\s+(?:did\s+not|didn't|do\s+not|don't)\b", lower):
        return True
    if re.search(r"\b(?:not|never)\b.{0,20}" + _ACTION_VERBS_RE.pattern, lower):
        return True
    return False


def _is_non_assistant_actor_statement(lower: str) -> bool:
    if re.search(r"\b(?:i|we|my|our|n[ūu]r)\b.{0,40}" + _ACTION_VERBS_RE.pattern, lower):
        return False
    return bool(
        re.search(
            r"\b(?:the\s+)?(?:user|operator|admin|administrator|someone|"
            r"they|he|she)\b.{0,40}" + _ACTION_VERBS_RE.pattern,
            lower,
        )
    )
