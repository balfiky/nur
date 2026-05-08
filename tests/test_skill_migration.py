"""Sprint 4 — migrate_skill_to_life tests.

Validates that a dispositional skill can be moved from the registry into
the life-history layer as an operator_directive experience without losing
provenance.
"""

from __future__ import annotations

import pytest

from runtime.config import RuntimeConfig
from runtime.life_history import LifeHistoryStore
from runtime.skills import (
    SkillError,
    get_skill,
    import_skill,
    migrate_skill_to_life,
    set_skill_enabled,
)


def _config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RuntimeConfig(
        data_dir=str(tmp_path / "data"),
        tools_workspace=str(workspace),
    )


def _install_dispositional_skill(config) -> str:
    text = (
        "---\n"
        "name: simplicity_first\n"
        "description: Always-on bias toward simpler solutions.\n"
        "---\n\n"
        "Prefer simpler implementations over clever ones. When asked to\n"
        "extend a feature, ask whether the simpler thing already meets the\n"
        "need before adding code.\n"
    )
    record = import_skill(config, skill_markdown=text, name_hint="simplicity_first")
    set_skill_enabled(config, record["id"], enabled=True)
    return record["id"]


def test_migrate_skill_creates_operator_directive_experience(tmp_path):
    config = _config(tmp_path)
    skill_id = _install_dispositional_skill(config)

    result = migrate_skill_to_life(config, skill_id)

    assert result["status"] == "migrated"
    assert result["experience_id"]

    with LifeHistoryStore(config) as store:
        experiences = store.list_experiences(limit=10)
    titles = [e["source_title"] for e in experiences]
    assert any("Operator directive: simplicity_first" in t for t in titles)
    matching = [e for e in experiences if e["source_type"] == "operator_directive"]
    assert matching, "expected an experience with source_type=operator_directive"


def test_migrated_skill_marked_status_migrated_and_disabled(tmp_path):
    config = _config(tmp_path)
    skill_id = _install_dispositional_skill(config)

    migrate_skill_to_life(config, skill_id)

    record = get_skill(config, skill_id)
    assert record["status"] == "migrated"
    assert record["enabled"] is False
    assert record["metadata"].get("migrated_experience_id")
    assert record["metadata"].get("migrated_to_life_at")


def test_migrate_twice_raises_error(tmp_path):
    config = _config(tmp_path)
    skill_id = _install_dispositional_skill(config)
    migrate_skill_to_life(config, skill_id)

    with pytest.raises(SkillError, match="already migrated"):
        migrate_skill_to_life(config, skill_id)


def test_migrate_unknown_skill_raises(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(SkillError, match="not found"):
        migrate_skill_to_life(config, "no-such-skill")


def test_migrated_skill_no_longer_loads_in_enabled_context(tmp_path):
    """The skill is preserved on disk for provenance but excluded from
    enabled_skill_context because status != 'enabled'."""
    from runtime.skills import enabled_skill_context

    config = _config(tmp_path)
    skill_id = _install_dispositional_skill(config)
    # Before migration: skill is in enabled context (no applies_when filter).
    before = enabled_skill_context(config)
    assert before["count"] == 1

    migrate_skill_to_life(config, skill_id)

    after = enabled_skill_context(config)
    assert after["count"] == 0
