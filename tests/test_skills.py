from __future__ import annotations

import pytest

from runtime.config import RuntimeConfig
from runtime.skills import (
    SkillError,
    audit_skill_text,
    enabled_skill_context,
    import_skill,
    list_skills,
    set_skill_enabled,
)
from runtime.tools import create_tool_executor


VALID_SKILL = """---
name: disk-helper
description: Inspect files and run shell commands for disk reports.
---

Use shell commands when needed. Read files, search files, and fetch URLs.
"""


def test_audit_skill_text_maps_agent_skill_metadata_and_tools():
    audit = audit_skill_text(VALID_SKILL)

    assert audit["compatible"] is True
    assert audit["metadata"]["name"] == "disk-helper"
    assert "shell.run_command" in audit["required_tools"]
    assert "fs.read_file" in audit["required_tools"]
    assert "web.fetch" in audit["required_tools"]
    assert "shell_access" in audit["risk_flags"]


def test_import_skill_installs_disabled_until_review(tmp_path):
    config = RuntimeConfig(data_dir=str(tmp_path / "data"))

    record = import_skill(config, skill_markdown=VALID_SKILL)

    assert record["id"] == "disk-helper"
    assert record["enabled"] is False
    assert record["status"] == "needs_review"
    assert record["compatibility"]["compatible"] is True

    listing = list_skills(config)
    assert listing["count"] == 1
    assert listing["skills"][0]["id"] == "disk-helper"

    enabled = set_skill_enabled(config, "disk-helper", True)
    assert enabled["enabled"] is True
    assert enabled["status"] == "enabled"

    disabled = set_skill_enabled(config, "disk-helper", False)
    assert disabled["enabled"] is False
    assert disabled["status"] == "disabled"


def test_invalid_skill_cannot_be_enabled(tmp_path):
    config = RuntimeConfig(data_dir=str(tmp_path / "data"))
    record = import_skill(
        config,
        skill_markdown="---\nname: broken\n---\nNo description.",
    )

    assert record["status"] == "invalid"
    assert record["compatibility"]["errors"]
    with pytest.raises(SkillError):
        set_skill_enabled(config, "broken", True)


def test_import_skill_from_folder_audits_scripts(tmp_path):
    source = tmp_path / "source-skill"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    (source / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
    (scripts / "run.py").write_text("print('ok')\n", encoding="utf-8")
    config = RuntimeConfig(data_dir=str(tmp_path / "data"))

    record = import_skill(config, source_path=str(source))

    audit = record["compatibility"]
    assert audit["files"]["script_count"] == 1
    assert "bundled_scripts" in audit["risk_flags"]
    assert "shell.run_command" in audit["required_tools"]


def test_enabled_skill_context_includes_only_enabled_bounded_guidance(tmp_path):
    config = RuntimeConfig(data_dir=str(tmp_path / "data"))
    import_skill(config, skill_markdown=VALID_SKILL)
    import_skill(
        config,
        skill_markdown="""---
name: unused
description: Disabled skill.
---

This should not enter runtime context.
""",
    )

    set_skill_enabled(config, "disk-helper", True)
    context = enabled_skill_context(config, max_instruction_chars=48)

    assert context["count"] == 1
    skill = context["skills"][0]
    assert skill["id"] == "disk-helper"
    assert skill["name"] == "disk-helper"
    assert "Use shell commands" in skill["instructions"]
    assert len(skill["instructions"]) <= 48
    assert "fs.read_file" in skill["required_tools"]
    assert "unused" not in {item["id"] for item in context["skills"]}


def test_skill_registry_tool_creates_and_enables_skill(tmp_path):
    config = RuntimeConfig(data_dir=str(tmp_path / "data"), tools_enabled=True)
    executor = create_tool_executor(config)
    assert executor is not None

    result = executor.execute(
        "skills.create_from_request",
        {
            "request": (
                "Create a skill for yourself to transform incoming reports "
                "into a concise action checklist using the current runtime tools."
            ),
            "enable": True,
        },
    )

    assert result.success is True
    assert "skill registry updated" in result.side_effect_summary
    listing = list_skills(config)
    assert listing["count"] == 1
    record = listing["skills"][0]
    assert record["enabled"] is True
    assert record["status"] == "enabled"

    context = enabled_skill_context(config)
    assert context["count"] == 1
    assert "action checklist" in context["skills"][0]["instructions"]


def test_skill_registry_tool_imports_markdown_then_enables(tmp_path):
    config = RuntimeConfig(data_dir=str(tmp_path / "data"), tools_enabled=True)
    executor = create_tool_executor(config)
    assert executor is not None

    imported = executor.execute(
        "skills.import_markdown",
        {"skill_markdown": VALID_SKILL, "enable": False},
    )
    assert imported.success is True
    assert list_skills(config)["skills"][0]["enabled"] is False

    enabled = executor.execute("skills.enable", {"skill_id": "disk-helper"})
    assert enabled.success is True
    assert list_skills(config)["skills"][0]["enabled"] is True
