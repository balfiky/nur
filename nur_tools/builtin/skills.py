"""Builtin skill-registry tools.

These tools let the cognitive tool loop update Nūr's imported skill registry
through the same observable ToolTrace path used for filesystem, web, and shell
actions. The implementation is intentionally generic: it creates/imports
operator-defined skill guidance and never special-cases a product, repository,
or transcript.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools.executor import ToolHandler
from runtime.config import RuntimeConfig
from runtime.skills import (
    SkillError,
    import_skill,
    list_skills,
    set_skill_enabled,
)


CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="skills.list",
        description="List imported runtime skills",
        category=ToolCategory.READ_ONLY,
        arg_schema={},
    ),
    ToolCapability(
        name="skills.create_from_request",
        description="Create and optionally enable a skill from an operator request",
        category=ToolCategory.COGNITIVE,
        arg_schema={
            "request": {"type": "string", "required": True},
            "name_hint": {"type": "string", "required": False},
            "source_url": {"type": "string", "required": False},
            "enable": {"type": "boolean", "required": False},
        },
    ),
    ToolCapability(
        name="skills.import_markdown",
        description="Import raw SKILL.md text into the runtime skill registry",
        category=ToolCategory.COGNITIVE,
        arg_schema={
            "skill_markdown": {"type": "string", "required": True},
            "name_hint": {"type": "string", "required": False},
            "enable": {"type": "boolean", "required": False},
        },
    ),
    ToolCapability(
        name="skills.enable",
        description="Enable an imported skill by id",
        category=ToolCategory.COGNITIVE,
        arg_schema={"skill_id": {"type": "string", "required": True}},
    ),
    ToolCapability(
        name="skills.disable",
        description="Disable an imported skill by id",
        category=ToolCategory.COGNITIVE,
        arg_schema={"skill_id": {"type": "string", "required": True}},
    ),
]


def create_handlers(config: RuntimeConfig | None = None) -> dict[str, ToolHandler]:
    """Return executable handlers bound to a runtime config."""
    runtime_config = config or RuntimeConfig()

    return {
        "skills.list": lambda args: _list(runtime_config, args),
        "skills.create_from_request": (
            lambda args: _create_from_request(runtime_config, args)
        ),
        "skills.import_markdown": lambda args: _import_markdown(runtime_config, args),
        "skills.enable": lambda args: _set_enabled(runtime_config, args, True),
        "skills.disable": lambda args: _set_enabled(runtime_config, args, False),
    }


def _list(config: RuntimeConfig, _args: dict[str, Any]) -> ToolResult:
    listing = list_skills(config)
    return ToolResult(
        tool_name="skills.list",
        success=True,
        output=_json_output({
            "count": listing["count"],
            "skills": [
                {
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "enabled": bool(item.get("enabled")),
                    "status": item.get("status", ""),
                }
                for item in listing.get("skills", [])
            ],
        }),
        metadata={"count": listing["count"], "root": listing["root"]},
        side_effect_summary="none",
    )


def _create_from_request(config: RuntimeConfig, args: dict[str, Any]) -> ToolResult:
    request = _clean_text(args.get("request"), limit=4000)
    if not request:
        return _error("skills.create_from_request", "request is required")

    name_hint = _clean_text(args.get("name_hint"), limit=120)
    source_url = _clean_text(args.get("source_url"), limit=400)
    enable = bool(args.get("enable", True))
    markdown = _skill_markdown_from_request(
        request,
        name_hint=name_hint,
        source_url=source_url,
    )
    return _import_markdown(
        config,
        {
            "skill_markdown": markdown,
            "name_hint": name_hint,
            "enable": enable,
        },
        tool_name="skills.create_from_request",
    )


def _import_markdown(
    config: RuntimeConfig,
    args: dict[str, Any],
    *,
    tool_name: str = "skills.import_markdown",
) -> ToolResult:
    skill_markdown = _clean_text(args.get("skill_markdown"), limit=20000)
    if not skill_markdown:
        return _error(tool_name, "skill_markdown is required")

    name_hint = _clean_text(args.get("name_hint"), limit=120)
    enable = bool(args.get("enable", False))
    try:
        record = import_skill(
            config,
            skill_markdown=skill_markdown,
            name_hint=name_hint,
        )
        if enable:
            record = set_skill_enabled(config, str(record["id"]), True)
    except SkillError as exc:
        return _error(tool_name, str(exc))

    payload = _record_payload(record)
    return ToolResult(
        tool_name=tool_name,
        success=True,
        output=_json_output(payload),
        metadata={
            "skill_id": payload["id"],
            "name": payload["name"],
            "enabled": payload["enabled"],
            "status": payload["status"],
        },
        side_effect_summary=(
            "skill registry updated: "
            f"imported {payload['id']}; enabled={str(payload['enabled']).lower()}"
        ),
    )


def _set_enabled(
    config: RuntimeConfig,
    args: dict[str, Any],
    enabled: bool,
) -> ToolResult:
    tool_name = "skills.enable" if enabled else "skills.disable"
    skill_id = _clean_text(args.get("skill_id"), limit=120)
    if not skill_id:
        return _error(tool_name, "skill_id is required")
    try:
        record = set_skill_enabled(config, skill_id, enabled)
    except SkillError as exc:
        return _error(tool_name, str(exc))
    payload = _record_payload(record)
    return ToolResult(
        tool_name=tool_name,
        success=True,
        output=_json_output(payload),
        metadata={
            "skill_id": payload["id"],
            "name": payload["name"],
            "enabled": payload["enabled"],
            "status": payload["status"],
        },
        side_effect_summary=(
            "skill registry updated: "
            f"{'enabled' if enabled else 'disabled'} {payload['id']}"
        ),
    )


def _skill_markdown_from_request(
    request: str,
    *,
    name_hint: str = "",
    source_url: str = "",
) -> str:
    name = _infer_skill_name(request, name_hint)
    description = _description_from_request(request, name)
    source_line = f"- Source reference: {source_url}\n" if source_url else ""
    return (
        "---\n"
        f"name: {_yaml_scalar(name)}\n"
        f"description: {_yaml_scalar(description)}\n"
        "---\n\n"
        f"# {name}\n\n"
        "## Operator Request\n"
        f"{request}\n\n"
        "## Use When\n"
        "- The user asks for work matching the operator request above.\n"
        "- The task can be completed with currently enabled runtime tools or "
        "by giving precise instructions when tools are unavailable.\n\n"
        "## Procedure\n"
        f"{source_line}"
        "- Identify the user's concrete input, desired output, and constraints.\n"
        "- Use the runtime tools that are available for the requested action.\n"
        "- If the operator named an execution environment or command wrapper, "
        "preserve that constraint when proposing or running commands.\n"
        "- Report only observed tool results. Do not invent files, command "
        "output, installed packages, or registry state.\n\n"
        "## Output\n"
        "- Return the result path, status, or next required input in concise text.\n"
    )


def _infer_skill_name(request: str, name_hint: str) -> str:
    explicit = _slugify(name_hint)
    if explicit:
        return explicit

    named = re.search(
        r"\b(?:called|named)\s+([A-Za-z0-9][A-Za-z0-9 _.-]{1,80})",
        request,
        flags=re.I,
    )
    if named:
        explicit = _slugify(named.group(1))
        if explicit:
            return explicit

    purpose = re.search(
        r"\b(?:skill|capability|module|integration)\b.{0,50}\b(?:to|for)\s+"
        r"([A-Za-z0-9][A-Za-z0-9 _./:-]{2,100})",
        request,
        flags=re.I | re.S,
    )
    if purpose:
        explicit = _slugify(_strip_urls(purpose.group(1)))
        if explicit:
            return explicit

    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z0-9]+", _strip_urls(request))
        if word.lower() not in _STOP_WORDS and len(word) > 2
    ]
    if words:
        return _slugify(" ".join(words[:5])) or "operator-skill"
    return "operator-skill"


def _description_from_request(request: str, name: str) -> str:
    compact = " ".join(_strip_urls(request).split())
    if compact:
        compact = compact[:180].rstrip(" .,;:")
        return f"Operator-defined skill for: {compact}"
    return f"Operator-defined skill: {name}"


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", " ", text or "")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(text or "").lower()).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return ""
    return slug[:80].strip("-._") or ""


def _clean_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def _json_output(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True)


def _yaml_scalar(value: str) -> str:
    return json.dumps(str(value or ""))


def _record_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record.get("id") or ""),
        "name": str(record.get("name") or ""),
        "description": str(record.get("description") or ""),
        "enabled": bool(record.get("enabled")),
        "status": str(record.get("status") or ""),
    }


def _error(tool_name: str, message: str) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=False,
        output="",
        error=message,
        side_effect_summary="none",
    )


_STOP_WORDS = {
    "able",
    "about",
    "and",
    "are",
    "based",
    "can",
    "create",
    "for",
    "from",
    "have",
    "make",
    "module",
    "please",
    "skill",
    "that",
    "the",
    "this",
    "using",
    "with",
    "you",
    "your",
    "yourself",
}
