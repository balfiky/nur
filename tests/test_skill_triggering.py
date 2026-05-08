"""Sprint 3 skill triggering tests.

Validates that:
  - Skills without applies_when load unconditionally (backward compat).
  - Skills with applies_when load only when context_hint tokens overlap.
  - audit emits a warning (not error) when applies_when is missing.
"""

from __future__ import annotations

from runtime.config import RuntimeConfig
from runtime.skills import (
    audit_skill_text,
    enabled_skill_context,
    import_skill,
    set_skill_enabled,
)


def _config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RuntimeConfig(
        data_dir=str(tmp_path / "data"),
        tools_workspace=str(workspace),
    )


def _install_skill(config, *, name: str, description: str, applies_when: str = "", body: str = "Use the skill.") -> str:
    """Install and enable a skill, returning its id."""
    frontmatter_lines = ["---", f"name: {name}", f"description: {description}"]
    if applies_when:
        frontmatter_lines.append(f"applies_when: {applies_when}")
    frontmatter_lines.append("---")
    text = "\n".join(frontmatter_lines) + "\n\n" + body
    record = import_skill(config, skill_markdown=text, name_hint=name)
    skill_id = record["id"]
    set_skill_enabled(config, skill_id, enabled=True)
    return skill_id


# ---------------------------------------------------------------------------
# audit warns (not errors) on missing applies_when
# ---------------------------------------------------------------------------

def test_audit_warns_when_applies_when_missing():
    text = "---\nname: tester\ndescription: A skill without trigger condition.\n---\n\nBody."
    audit = audit_skill_text(text)
    assert audit["compatible"] is True
    assert audit["errors"] == []
    assert any("applies_when" in str(w) for w in audit["warnings"])


def test_audit_does_not_warn_when_applies_when_present():
    text = (
        "---\nname: tester\ndescription: A skill with trigger.\n"
        "applies_when: pdf or extract\n---\n\nBody."
    )
    audit = audit_skill_text(text)
    assert audit["compatible"] is True
    assert audit["errors"] == []
    assert not any("applies_when" in str(w) for w in audit["warnings"])


# ---------------------------------------------------------------------------
# enabled_skill_context filtering
# ---------------------------------------------------------------------------

def test_skill_without_applies_when_loads_unconditionally(tmp_path):
    config = _config(tmp_path)
    _install_skill(
        config,
        name="always_on",
        description="A disposition with no trigger.",
    )
    # No context_hint at all
    ctx = enabled_skill_context(config)
    assert ctx["count"] == 1
    # With a hint that doesn't match anything — still loads.
    ctx2 = enabled_skill_context(
        config, context_hint={"user_message": "totally unrelated"}
    )
    assert ctx2["count"] == 1


def test_skill_with_matching_user_message_loads(tmp_path):
    config = _config(tmp_path)
    _install_skill(
        config,
        name="pdf_helper",
        description="Help with PDFs.",
        applies_when="pdf or extract",
    )
    ctx = enabled_skill_context(
        config,
        context_hint={"user_message": "can you summarize this pdf for me"},
    )
    assert ctx["count"] == 1
    assert ctx["skills"][0]["name"] == "pdf_helper"


def test_skill_with_non_matching_user_message_excluded(tmp_path):
    config = _config(tmp_path)
    _install_skill(
        config,
        name="pdf_helper",
        description="Help with PDFs.",
        applies_when="pdf or extract",
    )
    ctx = enabled_skill_context(
        config,
        context_hint={"user_message": "what's the weather like today"},
    )
    assert ctx["count"] == 0


def test_skill_matches_on_tool_name_hint(tmp_path):
    config = _config(tmp_path)
    _install_skill(
        config,
        name="fs_helper",
        description="Help with file ops.",
        applies_when="fs.read_file",
    )
    ctx = enabled_skill_context(
        config,
        context_hint={"tool_name": "fs.read_file"},
    )
    assert ctx["count"] == 1


def test_no_context_hint_disables_filtering_for_backward_compat(tmp_path):
    """When no context_hint is provided at all, even triggered skills must
    load — older callers must not see fewer skills than they did pre-Sprint-3."""
    config = _config(tmp_path)
    _install_skill(
        config,
        name="pdf_helper",
        description="Help with PDFs.",
        applies_when="pdf or extract",
    )
    ctx = enabled_skill_context(config, context_hint=None)
    assert ctx["count"] == 1


def test_mixed_skills_filter_independently(tmp_path):
    config = _config(tmp_path)
    _install_skill(
        config,
        name="always_on",
        description="No trigger.",
    )
    _install_skill(
        config,
        name="pdf_helper",
        description="Triggered skill.",
        applies_when="pdf",
    )
    ctx = enabled_skill_context(
        config,
        context_hint={"user_message": "can you help with this csv file"},
    )
    names = {s["name"] for s in ctx["skills"]}
    # always_on loads (no trigger), pdf_helper does not.
    assert "always_on" in names
    assert "pdf_helper" not in names


def test_context_hint_tokens_returned_for_observability(tmp_path):
    config = _config(tmp_path)
    _install_skill(config, name="foo", description="x")
    ctx = enabled_skill_context(
        config,
        context_hint={"user_message": "Read the PDF carefully"},
    )
    tokens = ctx["context_hint_tokens"]
    assert "pdf" in tokens
    assert "read" in tokens
