"""Skill import, audit, and runtime prompt-context support.

Imported Agent Skills are installed locally, audited, and disabled until an
operator enables them. Enabled skills are exposed to generation as bounded
private guidance. Skill scripts and resources are never executed by this module;
real actions still go through Nūr's normal tool gates.

Skills vs life-history seeds (Sprint 4 categorization rule):
  - Procedural skill = an invokable craft with a clear trigger condition
    ("when I'm doing PDFs, use this skill"). These belong in the skill registry
    and should declare ``applies_when`` so they only load when relevant.
  - Dispositional content = always-on values or behavioral guidelines
    ("ask before destructive actions", "prefer simplicity"). These belong in
    the life-history layer (operator constitution or life seeds), not as
    skills, because they shape decision-making, not execution.

Use ``migrate_skill_to_life`` to move a dispositional skill out of the registry
and into the life-history ledger as an ``operator_directive`` experience.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from runtime.config import RuntimeConfig


REGISTRY_VERSION = 1
SKILL_FILENAME = "SKILL.md"
_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_IGNORE_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}


class SkillError(ValueError):
    """Raised when a skill import/audit operation cannot be completed."""


def list_skills(config: RuntimeConfig) -> dict[str, Any]:
    root = skills_root(config)
    registry = _read_registry(root)
    skills = sorted(
        registry.get("skills", []),
        key=lambda item: (str(item.get("name", "")).lower(), str(item.get("id", ""))),
    )
    return {
        "root": str(root),
        "count": len(skills),
        "skills": skills,
    }


def enabled_skill_context(
    config: RuntimeConfig,
    *,
    limit: int = 5,
    max_instruction_chars: int = 1200,
    context_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return bounded instructions for enabled, compatible skills.

    The generator receives this context as private guidance. It is deliberately
    text-only and omits bundled scripts/resources so importing a community skill
    cannot bypass runtime tool policy.

    When ``context_hint`` is provided (typically containing ``user_message``
    and/or ``tool_name``), skills whose ``applies_when`` frontmatter does not
    match the hint are filtered out. Skills with no ``applies_when`` field
    keep current always-on behavior for backward compatibility.
    """
    root = skills_root(config)
    registry = _read_registry(root)
    hint_tokens = _context_hint_tokens(context_hint)
    skills: list[dict[str, Any]] = []
    for record in registry.get("skills", []):
        if not record.get("enabled") or record.get("status") != "enabled":
            continue
        compatibility = record.get("compatibility") or {}
        if compatibility.get("errors"):
            continue
        skill_root = Path(str(record.get("root") or ""))
        skill_file = skill_root / SKILL_FILENAME
        if not skill_file.is_file():
            continue
        metadata, body, warnings = _parse_frontmatter(
            skill_file.read_text(encoding="utf-8", errors="replace")
        )
        if warnings:
            continue
        applies_when = str(metadata.get("applies_when") or "").strip()
        if applies_when and hint_tokens and not _trigger_matches(applies_when, hint_tokens):
            continue
        skills.append({
            "id": record.get("id") or "",
            "name": metadata.get("name") or record.get("name") or record.get("id") or "",
            "description": (
                metadata.get("description")
                or record.get("description")
                or ""
            ),
            "instructions": _trim_runtime_text(body, max_instruction_chars),
            "required_tools": list(compatibility.get("required_tools") or []),
            "risk_flags": list(compatibility.get("risk_flags") or []),
            "applies_when": applies_when,
        })
        if len(skills) >= max(1, limit):
            break
    return {
        "root": str(root),
        "count": len(skills),
        "skills": skills,
        "context_hint_tokens": sorted(hint_tokens),
    }


def _context_hint_tokens(context_hint: dict[str, Any] | None) -> set[str]:
    if not context_hint:
        return set()
    parts: list[str] = []
    for key in ("user_message", "tool_name", "response_strategy"):
        value = context_hint.get(key)
        if value:
            parts.append(str(value))
    extras = context_hint.get("extras")
    if isinstance(extras, (list, tuple)):
        parts.extend(str(item) for item in extras if item)
    blob = " ".join(parts).lower()
    # Tokenize on word boundaries; keep alphanumeric + dot for tool names like fs.read_file.
    return set(re.findall(r"[a-z0-9][a-z0-9._-]*", blob))


def _trigger_matches(applies_when: str, hint_tokens: set[str]) -> bool:
    """Return True if any token in applies_when appears in the context hint."""
    trigger_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", applies_when.lower()))
    return bool(trigger_tokens & hint_tokens)


def import_skill(
    config: RuntimeConfig,
    *,
    source_path: str | None = None,
    skill_markdown: str | None = None,
    name_hint: str = "",
) -> dict[str, Any]:
    """Install a skill folder or raw SKILL.md text into ``data/skills``.

    Imported skills are always disabled and marked ``needs_review`` unless the
    audit finds hard errors, in which case they are marked ``invalid``.
    """
    source_path = (source_path or "").strip()
    skill_markdown = (skill_markdown or "").strip()
    if bool(source_path) == bool(skill_markdown):
        raise SkillError("Provide exactly one of source_path or skill_markdown.")

    source_root: Path | None = None
    source_label = "pasted"
    if source_path:
        source_root, skill_text = _read_source_skill(source_path)
        source_label = str(source_root)
    else:
        skill_text = skill_markdown

    initial_audit = audit_skill_text(skill_text, source_root=source_root)
    metadata = initial_audit["metadata"]
    display_name = str(metadata.get("name") or name_hint or "imported-skill").strip()
    skill_id = _unique_skill_id(skills_root(config), display_name)
    dest = skills_root(config) / skill_id
    dest.parent.mkdir(parents=True, exist_ok=True)

    if source_root is not None:
        shutil.copytree(
            source_root,
            dest,
            ignore=shutil.ignore_patterns(*sorted(_IGNORE_NAMES)),
        )
    else:
        dest.mkdir(parents=True, exist_ok=False)
        (dest / SKILL_FILENAME).write_text(skill_text + "\n", encoding="utf-8")

    audit = audit_skill_path(dest)
    record = {
        "id": skill_id,
        "name": audit["metadata"].get("name") or display_name,
        "description": audit["metadata"].get("description", ""),
        "enabled": False,
        "status": "invalid" if audit["errors"] else "needs_review",
        "source": source_label,
        "root": str(dest),
        "installed_at": time.time(),
        "updated_at": time.time(),
        "checksum": _tree_checksum(dest),
        "metadata": audit["metadata"],
        "compatibility": audit,
    }
    registry = _read_registry(skills_root(config))
    registry["skills"] = [
        item for item in registry.get("skills", [])
        if item.get("id") != skill_id
    ] + [record]
    _write_registry(skills_root(config), registry)
    return record


def get_skill(config: RuntimeConfig, skill_id: str) -> dict[str, Any]:
    skill_id = _validate_skill_id(skill_id)
    record = _find_skill(config, skill_id)
    if record is None:
        raise SkillError(f"Skill not found: {skill_id}")
    return record


def audit_installed_skill(config: RuntimeConfig, skill_id: str) -> dict[str, Any]:
    skill_id = _validate_skill_id(skill_id)
    registry = _read_registry(skills_root(config))
    record = _find_skill_in_registry(registry, skill_id)
    if record is None:
        raise SkillError(f"Skill not found: {skill_id}")
    root = Path(str(record.get("root") or ""))
    audit = audit_skill_path(root)
    record["name"] = audit["metadata"].get("name") or record.get("name") or skill_id
    record["description"] = audit["metadata"].get("description", "")
    record["metadata"] = audit["metadata"]
    record["compatibility"] = audit
    record["checksum"] = _tree_checksum(root) if root.exists() else ""
    record["updated_at"] = time.time()
    if audit["errors"]:
        record["enabled"] = False
        record["status"] = "invalid"
    elif record.get("enabled"):
        record["status"] = "enabled"
    elif record.get("status") == "disabled":
        record["status"] = "disabled"
    else:
        record["status"] = "needs_review"
    _write_registry(skills_root(config), registry)
    return record


def set_skill_enabled(config: RuntimeConfig, skill_id: str, enabled: bool) -> dict[str, Any]:
    skill_id = _validate_skill_id(skill_id)
    registry = _read_registry(skills_root(config))
    record = _find_skill_in_registry(registry, skill_id)
    if record is None:
        raise SkillError(f"Skill not found: {skill_id}")
    root = Path(str(record.get("root") or ""))
    audit = audit_skill_path(root)
    record["name"] = audit["metadata"].get("name") or record.get("name") or skill_id
    record["description"] = audit["metadata"].get("description", "")
    record["metadata"] = audit["metadata"]
    record["compatibility"] = audit
    record["checksum"] = _tree_checksum(root) if root.exists() else ""
    record["updated_at"] = time.time()
    if enabled and audit.get("errors"):
        record["enabled"] = False
        record["status"] = "invalid"
        _write_registry(skills_root(config), registry)
        raise SkillError("Cannot enable a skill with audit errors.")
    if audit.get("errors"):
        record["enabled"] = False
        record["status"] = "invalid"
        _write_registry(skills_root(config), registry)
        return record
    record["enabled"] = bool(enabled)
    record["status"] = "enabled" if enabled else "disabled"
    _write_registry(skills_root(config), registry)
    return record


def delete_skill(config: RuntimeConfig, skill_id: str) -> dict[str, Any]:
    """Remove an imported skill from disk and from the registry."""
    skill_id = _validate_skill_id(skill_id)
    root = skills_root(config)
    registry = _read_registry(root)
    record = _find_skill_in_registry(registry, skill_id)
    if record is None:
        raise SkillError(f"Skill not found: {skill_id}")

    skill_root = Path(str(record.get("root") or root / skill_id)).expanduser().resolve()
    root_resolved = root.resolve()
    root_removed = False
    if skill_root.exists():
        try:
            skill_root.relative_to(root_resolved)
        except ValueError as exc:
            raise SkillError("Refusing to delete a skill outside the skills root.") from exc
        if skill_root.is_dir():
            shutil.rmtree(skill_root)
            root_removed = True
        else:
            skill_root.unlink()
            root_removed = True

    registry["skills"] = [
        item for item in registry.get("skills", [])
        if item.get("id") != skill_id
    ]
    _write_registry(root, registry)
    return {
        "id": skill_id,
        "name": record.get("name") or skill_id,
        "root": str(skill_root),
        "deleted": True,
        "root_removed": root_removed,
    }


def migrate_skill_to_life(
    config: RuntimeConfig,
    skill_id: str,
    *,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Move a dispositional skill out of the registry and into life history.

    Reads the skill body, ingests it as an experience with
    ``source_type='operator_directive'`` so the content becomes part of
    Nūr's evolving worldview rather than always-on prompt guidance. The
    original skill is marked ``status='migrated'`` and disabled, but its
    files remain on disk for provenance.

    The decision of whether to migrate is operator-driven (this helper does
    not classify automatically). Use it when a skill's content is values /
    dispositions / always-on guidelines, not invokable procedural craft.
    """
    skill_id = _validate_skill_id(skill_id)
    root = skills_root(config)
    registry = _read_registry(root)
    record = _find_skill_in_registry(registry, skill_id)
    if record is None:
        raise SkillError(f"Skill not found: {skill_id}")
    if record.get("status") == "migrated":
        raise SkillError(f"Skill already migrated: {skill_id}")

    skill_file = Path(str(record.get("root") or "")) / SKILL_FILENAME
    if not skill_file.is_file():
        raise SkillError(f"Skill file missing: {skill_file}")

    metadata, body, _warnings = _parse_frontmatter(
        skill_file.read_text(encoding="utf-8", errors="replace")
    )
    title = str(metadata.get("name") or record.get("name") or skill_id)
    description = str(metadata.get("description") or "")
    seed_text = body.strip() or description
    if not seed_text:
        raise SkillError("Skill has no body to migrate.")

    from runtime.life_history import LifeHistoryStore

    with LifeHistoryStore(config) as store:
        ingest = store.ingest_pasted_text(
            title=f"Operator directive: {title}",
            text=seed_text,
            source_type="operator_directive",
            participants=["operator"],
            llm_client=llm_client,
        )

    record["enabled"] = False
    record["status"] = "migrated"
    record["updated_at"] = time.time()
    metadata_dict = dict(record.get("metadata") or {})
    metadata_dict["migrated_to_life_at"] = time.time()
    metadata_dict["migrated_experience_id"] = ingest["experience"]["id"]
    record["metadata"] = metadata_dict
    _write_registry(root, registry)

    return {
        "id": skill_id,
        "name": title,
        "status": "migrated",
        "experience_id": ingest["experience"]["id"],
        "experience": ingest["experience"],
    }


def audit_skill_path(path: Path) -> dict[str, Any]:
    skill_file = path / SKILL_FILENAME
    if not skill_file.is_file():
        return _audit_payload(
            metadata={},
            body="",
            errors=[f"{SKILL_FILENAME} not found"],
            warnings=[],
            source_root=path,
        )
    text = skill_file.read_text(encoding="utf-8", errors="replace")
    return audit_skill_text(text, source_root=path)


def audit_skill_text(text: str, *, source_root: Path | None = None) -> dict[str, Any]:
    metadata, body, parse_warnings = _parse_frontmatter(text)
    errors: list[str] = []
    warnings: list[str] = list(parse_warnings)
    if not str(metadata.get("name", "")).strip():
        errors.append("Frontmatter field 'name' is required.")
    if not str(metadata.get("description", "")).strip():
        errors.append("Frontmatter field 'description' is required.")
    if not str(metadata.get("applies_when", "")).strip():
        warnings.append(
            "Frontmatter field 'applies_when' is missing — this skill will load "
            "for every generation. Add a trigger condition (e.g. tool name or "
            "user-message keywords) so it only loads when relevant."
        )
    if source_root is not None and not (source_root / SKILL_FILENAME).is_file():
        errors.append(f"{SKILL_FILENAME} not found under source root.")
    return _audit_payload(
        metadata=metadata,
        body=body,
        errors=errors,
        warnings=warnings,
        source_root=source_root,
    )


def skills_root(config: RuntimeConfig) -> Path:
    return Path(config.data_dir).expanduser().resolve() / "skills"


def _audit_payload(
    *,
    metadata: dict[str, Any],
    body: str,
    errors: list[str],
    warnings: list[str],
    source_root: Path | None,
) -> dict[str, Any]:
    text = f"{json.dumps(metadata, sort_keys=True)}\n{body}"
    mappings = _infer_tool_mappings(text, source_root)
    risk_flags = _infer_risk_flags(text, source_root)
    unsupported = _infer_unsupported_features(text)
    files = _scan_skill_files(source_root)
    if unsupported:
        warnings.append("Some platform-specific features may need custom Nūr adapters.")
    if files["script_count"]:
        warnings.append("Skill includes scripts. Nūr imports them for review but does not execute them during import.")
    return {
        "compatible": not errors,
        "errors": errors,
        "warnings": _dedupe(warnings),
        "metadata": metadata,
        "required_tools": sorted({item["nur_tool"] for item in mappings if item["nur_tool"]}),
        "tool_mappings": mappings,
        "risk_flags": risk_flags,
        "unsupported_features": unsupported,
        "files": files,
    }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text, ["No YAML frontmatter block found."]
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", stripped, flags=re.S)
    if not match:
        return {}, text, ["YAML frontmatter block is not closed."]
    raw_meta, body = match.groups()
    try:
        import yaml

        data = yaml.safe_load(raw_meta) or {}
    except Exception as exc:
        return {}, body, [f"Could not parse YAML frontmatter: {exc}"]
    if not isinstance(data, dict):
        return {}, body, ["YAML frontmatter must be a mapping."]
    return data, body, []


def _infer_tool_mappings(text: str, source_root: Path | None) -> list[dict[str, Any]]:
    lower = text.lower()
    rules = [
        ("shell or CLI execution", "shell.run_command", r"\b(shell|bash|terminal|cli|subprocess|run command|execute command)\b"),
        ("filesystem reads", "fs.read_file", r"\b(read|open|load)\s+(a\s+)?files?\b"),
        ("filesystem writes", "fs.write_file", r"\b(write|edit|save|create)\s+(a\s+)?files?\b"),
        ("text search", "fs.search_text", r"\b(grep|ripgrep|search text|search files?)\b"),
        ("glob file discovery", "fs.glob_paths", r"\b(glob|find files?|file pattern)\b"),
        ("web search", "web.search", r"\b(web search|search the web|internet search|google)\b"),
        ("web fetch", "web.fetch", r"\b(fetch|download|http|https|url)\b"),
        ("browser automation", "browser.open_url", r"\b(browser|click|fill form|screenshot|web page)\b"),
    ]
    mappings = [
        {
            "hint": hint,
            "nur_tool": nur_tool,
            "status": "mapped",
        }
        for hint, nur_tool, pattern in rules
        if re.search(pattern, lower)
    ]
    if source_root is not None and (source_root / "scripts").is_dir():
        mappings.append({
            "hint": "bundled scripts",
            "nur_tool": "shell.run_command",
            "status": "review_required",
        })
    return _dedupe_mappings(mappings)


def _infer_risk_flags(text: str, source_root: Path | None) -> list[str]:
    lower = text.lower()
    flags: list[str] = []
    checks = [
        ("shell_access", r"\b(shell|bash|terminal|subprocess|run command|execute command|sudo)\b"),
        ("filesystem_write", r"\b(write|edit|save|delete|remove|overwrite|create file)\b"),
        ("network_access", r"\b(http|https|web search|download|api key|token)\b"),
        ("destructive_actions", r"\b(delete|remove|prune|drop|wipe|rm -rf|format)\b"),
        ("secrets_or_env", r"\b(api key|token|secret|env|environment variable)\b"),
        ("installer_metadata", r"\b(install|pip install|npm install|brew install|uv add)\b"),
    ]
    for flag, pattern in checks:
        if re.search(pattern, lower):
            flags.append(flag)
    if source_root is not None and (source_root / "scripts").is_dir():
        flags.append("bundled_scripts")
    return sorted(set(flags))


def _infer_unsupported_features(text: str) -> list[str]:
    lower = text.lower()
    checks = [
        ("mcp_server", r"\bmcp\b"),
        ("slack_integration", r"\bslack\b"),
        ("discord_integration", r"\bdiscord\b"),
        ("github_specific_api", r"\bgithub\b"),
        ("docker_or_container", r"\b(docker|container)\b"),
        ("kubernetes", r"\bkubernetes|kubectl\b"),
    ]
    return [name for name, pattern in checks if re.search(pattern, lower)]


def _scan_skill_files(source_root: Path | None) -> dict[str, Any]:
    if source_root is None or not source_root.exists():
        return {"total_count": 0, "script_count": 0, "scripts": [], "resource_count": 0}
    total = 0
    scripts: list[str] = []
    resources = 0
    for path in source_root.rglob("*"):
        if any(part in _IGNORE_NAMES for part in path.parts):
            continue
        if path.is_file():
            total += 1
            rel = str(path.relative_to(source_root))
            if rel.startswith("scripts/"):
                scripts.append(rel)
            elif rel != SKILL_FILENAME:
                resources += 1
    return {
        "total_count": total,
        "script_count": len(scripts),
        "scripts": scripts[:50],
        "resource_count": resources,
    }


def _read_source_skill(source_path: str) -> tuple[Path, str]:
    raw = Path(source_path).expanduser()
    path = raw.resolve()
    if not path.exists():
        raise SkillError(f"Source path does not exist: {source_path}")
    skill_file = path if path.is_file() and path.name == SKILL_FILENAME else path / SKILL_FILENAME
    if not skill_file.is_file():
        raise SkillError(f"{SKILL_FILENAME} not found at source path.")
    return skill_file.parent, skill_file.read_text(encoding="utf-8", errors="replace")


def _tree_checksum(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return ""
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if any(part in _IGNORE_NAMES for part in path.parts):
            continue
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_registry(root: Path) -> dict[str, Any]:
    path = _registry_path(root)
    if not path.exists():
        return {"version": REGISTRY_VERSION, "skills": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillError(f"Skill registry is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillError("Skill registry must be a JSON object.")
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("skills", [])
    if not isinstance(data["skills"], list):
        raise SkillError("Skill registry 'skills' field must be a list.")
    return data


def _write_registry(root: Path, registry: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _registry_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _registry_path(root: Path) -> Path:
    return root / "registry.json"


def _find_skill(config: RuntimeConfig, skill_id: str) -> dict[str, Any] | None:
    return _find_skill_in_registry(_read_registry(skills_root(config)), skill_id)


def _find_skill_in_registry(registry: dict[str, Any], skill_id: str) -> dict[str, Any] | None:
    for item in registry.get("skills", []):
        if item.get("id") == skill_id:
            return item
    return None


def _unique_skill_id(root: Path, name: str) -> str:
    registry = _read_registry(root)
    existing = {item.get("id") for item in registry.get("skills", [])}
    base = _slugify(name) or "skill"
    candidate = base
    counter = 2
    while candidate in existing or (root / candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-._")
    return slug[:80].strip("-._")


def _validate_skill_id(skill_id: str) -> str:
    skill_id = (skill_id or "").strip()
    if not _SKILL_ID_RE.fullmatch(skill_id):
        raise SkillError("Invalid skill id.")
    return skill_id


def _trim_runtime_text(text: str, limit: int) -> str:
    clean = "\n".join(
        line.rstrip()
        for line in str(text or "").strip().splitlines()
    )
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _dedupe_mappings(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        key = (str(item.get("hint")), str(item.get("nur_tool")))
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out
