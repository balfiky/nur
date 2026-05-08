"""End-to-end UAT for the Sprint 1-5 evolution features.

Covers the surfaces added between commits 1430d77 (metabolism) and d056c37
(ask-user surfacing): constitution layer, open-questions queue, metabolism
tick endpoint, trigger-time skill retrieval, and ask-user surfacing.

Open questions are seeded by writing directly to the same SQLite database
the running server uses (SQLite supports concurrent connections). This keeps
the tests deterministic without needing an LLM-driven path to fire.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from runtime.config import RuntimeConfig
from runtime.life_history import LifeHistoryStore
from tests.uat.conftest import assert_no_browser_errors, expect_json

pytestmark = pytest.mark.uat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_store(uat_server) -> LifeHistoryStore:
    """Open the same life-history DB the running server is using."""
    config = RuntimeConfig.from_yaml(str(uat_server.config_path))
    return LifeHistoryStore(config)


def _seed_open_question(uat_server, *, prompt: str, source_kind: str = "drive_gap",
                        target_drive: str | None = None, priority: float = 0.7) -> int:
    """Seed an open question directly. Returns the inserted question id.

    Uses ``_emit_open_question`` (Python has no real privacy) because no public
    seeding endpoint exists — open questions are normally produced by
    reflection on real history. Commits explicitly because ``_emit_open_question``
    leaves the transaction open for batch callers.
    """
    with _open_store(uat_server) as store:
        store._emit_open_question(
            prompt_text=prompt,
            source_kind=source_kind,
            target_drive=target_drive,
            priority=priority,
            dedupe_signature=f"uat-{prompt[:40]}",
        )
        store._conn.commit()
        questions = store.list_open_questions(status="open", limit=200)
    for q in questions:
        if q["prompt_text"] == prompt:
            return int(q["id"])
    raise AssertionError(f"Seeded question not found: {prompt!r}")


# ---------------------------------------------------------------------------
# Constitution layer
# ---------------------------------------------------------------------------


def _put_constitution(uat_server, text: str):
    import requests
    return requests.put(
        uat_server.base_url + "/admin/identity/constitution",
        json={"constitution": text},
        timeout=15,
    )


def test_constitution_persists_via_api_and_restart(uat_server):
    """Constitution: GET/PUT round-trip, trim/dedupe, survives restart."""
    initial = expect_json(uat_server.get("/admin/identity/constitution"))
    assert initial["constitution"] == ""
    seed_ts = initial["updated_at"]  # seeded on DB init

    set_data = expect_json(
        _put_constitution(uat_server, "  Care about correctness over cleverness.  ")
    )
    assert set_data["ok"] is True
    # Whitespace trimmed.
    assert set_data["constitution"] == "Care about correctness over cleverness."
    assert set_data["updated_at"] >= seed_ts

    update_data = expect_json(
        _put_constitution(uat_server, "Prefer simplicity over flexibility.")
    )
    assert update_data["constitution"] == "Prefer simplicity over flexibility."
    assert update_data["updated_at"] >= set_data["updated_at"]

    # Survive a server restart.
    uat_server.restart()
    after = expect_json(uat_server.get("/admin/identity/constitution"))
    assert after["constitution"] == "Prefer simplicity over flexibility."
    assert after["updated_at"] > 0.0


def test_constitution_ui_loads_and_saves(uat_server, page):
    """UI surface: textarea reflects current value and Save persists edits."""
    expect_json(_put_constitution(uat_server, "Original orientation."))

    page.goto(uat_server.base_url + "/settings#life")
    expect(page.locator("#page-life")).to_be_visible()
    expect(page.locator("#constitutionText")).to_have_value(
        "Original orientation.", timeout=15_000,
    )
    expect(page.locator("#constitutionUpdated")).to_contain_text("last updated")

    new_text = "Honesty before convenience. Keep responses concise."
    # Set the value just before save inside the same event-loop turn so the
    # admin SPA can't re-fetch and overwrite it. ``page.fill`` settles between
    # microtasks and lets renderLife's deferred loadConstitution clobber the
    # field; doing the assignment inline with the click avoids that race.
    page.evaluate(
        """([sel, value]) => {
            const el = document.querySelector(sel);
            el.value = value;
            document.getElementById('saveConstitutionBtn').click();
        }""",
        ["#constitutionText", new_text],
    )
    expect(page.locator("#toast")).to_contain_text("Constitution saved", timeout=15_000)

    expect(page.locator("#toast")).to_contain_text("Constitution saved", timeout=15_000)

    final = expect_json(uat_server.get("/admin/identity/constitution"))
    assert final["constitution"] == new_text

    assert_no_browser_errors(page)


def test_constitution_max_length_is_enforced(uat_server):
    big = "x" * 3000
    res = _put_constitution(uat_server, big)
    # Pydantic max_length=2000 rejects anything longer with a 422.
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Open questions queue
# ---------------------------------------------------------------------------


def test_open_questions_lifecycle_via_api(uat_server):
    initial = expect_json(uat_server.get("/admin/life/open-questions"))
    assert initial["questions"] == []
    assert initial["counts"] == {"open": 0, "pursuing": 0, "resolved": 0, "abandoned": 0}

    qid_drive = _seed_open_question(
        uat_server,
        prompt="Why does my curiosity keep dropping after long sessions?",
        source_kind="drive_gap",
        target_drive="curiosity",
        priority=0.8,
    )
    qid_contradiction = _seed_open_question(
        uat_server,
        prompt="Earlier I claimed reading is always restful — but several entries contradict that.",
        source_kind="contradiction",
        priority=0.65,
    )
    qid_lowconf = _seed_open_question(
        uat_server,
        prompt="A pattern keeps recurring around late-night work but I'm not confident yet.",
        source_kind="low_confidence",
        priority=0.4,
    )

    listing = expect_json(uat_server.get("/admin/life/open-questions"))
    assert listing["counts"]["open"] == 3
    assert listing["counts"]["pursuing"] == 0

    # Sorted by priority desc — drive_gap (0.8) should come first.
    ids_in_order = [q["id"] for q in listing["questions"]]
    assert ids_in_order[0] == qid_drive
    assert qid_contradiction in ids_in_order
    assert qid_lowconf in ids_in_order

    # Filter by status.
    pursuing = expect_json(uat_server.get("/admin/life/open-questions?status=pursuing"))
    assert pursuing["questions"] == []

    # Bogus filter is rejected.
    bad = uat_server.get("/admin/life/open-questions?status=invalid")
    assert bad.status_code == 400

    # Abandon one, resolve another.
    abandoned = expect_json(
        uat_server.post(f"/admin/life/open-questions/{qid_lowconf}/abandon")
    )
    assert abandoned["status"] == "abandoned"

    resolved = expect_json(
        uat_server.post(f"/admin/life/open-questions/{qid_contradiction}/resolve")
    )
    assert resolved["status"] == "resolved"

    after = expect_json(uat_server.get("/admin/life/open-questions"))
    assert after["counts"] == {
        "open": 1, "pursuing": 0, "resolved": 1, "abandoned": 1,
    }
    open_ids = [q["id"] for q in after["questions"]]
    assert open_ids == [qid_drive]

    # Already-closed and unknown-id paths return 404.
    repeat = uat_server.post(f"/admin/life/open-questions/{qid_lowconf}/abandon")
    assert repeat.status_code == 404
    bogus = uat_server.post("/admin/life/open-questions/999999/resolve")
    assert bogus.status_code == 404

    # ?status=all returns everything.
    all_ = expect_json(uat_server.get("/admin/life/open-questions?status=all"))
    assert len(all_["questions"]) == 3


def test_open_questions_render_in_ui_and_abandon_button_works(uat_server, page):
    qid = _seed_open_question(
        uat_server,
        prompt="Is curiosity worth chasing when energy is already low?",
        source_kind="drive_gap",
        target_drive="curiosity",
        priority=0.7,
    )

    page.goto(uat_server.base_url + "/settings#life")
    expect(page.locator("#page-life")).to_be_visible()
    expect(page.locator("#lifeOpenQuestionsCounts")).to_contain_text("open 1", timeout=15_000)
    expect(page.locator("#lifeOpenQuestions")).to_contain_text(
        "Is curiosity worth chasing", timeout=15_000,
    )
    expect(page.locator("#lifeOpenQuestions")).to_contain_text("drive=curiosity")

    page.locator(f"[data-abandon-oq='{qid}']").click()
    expect(page.locator("#lifeOpenQuestionsCounts")).to_contain_text("open 0", timeout=15_000)
    expect(page.locator("#lifeOpenQuestions")).to_contain_text(
        "No open questions yet", timeout=15_000,
    )

    after = expect_json(uat_server.get("/admin/life/open-questions?status=abandoned"))
    assert any(q["id"] == qid for q in after["questions"])

    assert_no_browser_errors(page)


# ---------------------------------------------------------------------------
# Metabolism tick
# ---------------------------------------------------------------------------


def test_metabolism_tick_is_idempotent_within_a_day(uat_server):
    # ``_ensure_metabolism_state`` stamps last_decay_at when the DB is created,
    # so each tick computes a tiny elapsed delta. Both calls should be no-ops
    # because elapsed_days is well under the 1-day rate limit.
    first = expect_json(uat_server.post("/admin/life/metabolism/tick"))
    assert first["ok"] is True
    assert first.get("decayed") is False
    assert first.get("elapsed_days", 0.0) < 1.0

    second = expect_json(uat_server.post("/admin/life/metabolism/tick"))
    assert second["ok"] is True
    assert second.get("decayed") is False
    assert second.get("elapsed_days", 0.0) < 1.0
    # Decay never fired, so no consolidation work either.
    assert second.get("result") == {}


# ---------------------------------------------------------------------------
# Trigger-time skill retrieval
# ---------------------------------------------------------------------------


_TRIGGERED_SKILL = """---
name: uat-video-helper
description: Outline how to download and summarize video links.
applies_when: video download youtube
---

When the operator mentions video downloads, return three bullets covering
URL validation, format selection, and post-download summary.
"""


_ALWAYS_ON_SKILL = """---
name: uat-style-guide
description: Always-loaded house style guide for UAT.
---

Keep responses tight: prefer short sentences and avoid filler.
"""


def test_applies_when_filters_skills_in_chat_context(uat_server):
    # Both skills imported and enabled. The triggered one only loads when the
    # message hint mentions its trigger tokens.
    triggered = expect_json(
        uat_server.post(
            "/admin/skills/import",
            {"skill_markdown": _TRIGGERED_SKILL, "name_hint": "uat-video-helper"},
        )
    )["skill"]
    expect_json(uat_server.post(f"/admin/skills/{triggered['id']}/enable"))

    always = expect_json(
        uat_server.post(
            "/admin/skills/import",
            {"skill_markdown": _ALWAYS_ON_SKILL, "name_hint": "uat-style-guide"},
        )
    )["skill"]
    expect_json(uat_server.post(f"/admin/skills/{always['id']}/enable"))

    off_topic = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Hello, how are you today?",
                "user_id": "uat_skill_trigger",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    off_skills = off_topic["debug"]["skill_context"].get("skills", [])
    off_ids = {s["id"] for s in off_skills}
    assert always["id"] in off_ids, "Always-on skill should load regardless of hint"
    assert triggered["id"] not in off_ids, (
        f"Triggered skill loaded without matching hint: {off_ids!r}"
    )

    on_topic = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Help me with a video download workflow please.",
                "user_id": "uat_skill_trigger",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    on_skills = on_topic["debug"]["skill_context"].get("skills", [])
    on_ids = {s["id"] for s in on_skills}
    assert always["id"] in on_ids
    assert triggered["id"] in on_ids, (
        f"Triggered skill missing despite matching hint: {on_ids!r}"
    )


# ---------------------------------------------------------------------------
# Ask-user surfacing (Sprint 5)
# ---------------------------------------------------------------------------


def test_open_question_surfaces_in_chat_and_marks_pursuing(uat_server):
    qid = _seed_open_question(
        uat_server,
        prompt="What does autonomy mean to you in collaborative work?",
        source_kind="drive_gap",
        target_drive="curiosity",
        priority=0.9,
    )

    first = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Just checking in — anything interesting on your mind?",
                "user_id": "uat_ask_user",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    effects = first["debug"].get("life_influence_effects") or {}
    assert int(effects.get("open_question_surfaced", 0)) == qid, (
        f"Expected question {qid} surfaced; effects={effects!r}"
    )
    assert "wondering" in first["response"].lower(), (
        f"Surfacing follow-up missing from response: {first['response']!r}"
    )
    assert "autonomy mean to you in collaborative work" in first["response"]

    # DB transitioned to pursuing — operator-driven resolution only from here.
    pursuing = expect_json(
        uat_server.get("/admin/life/open-questions?status=pursuing")
    )
    assert any(q["id"] == qid for q in pursuing["questions"])

    # The same question does not re-surface on the next turn (it's no longer
    # 'open'), and with no other eligible question, no surfacing happens.
    second = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Got it. Continue.",
                "user_id": "uat_ask_user",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    second_effects = second["debug"].get("life_influence_effects") or {}
    assert "open_question_surfaced" not in second_effects, (
        f"Second turn should not re-surface; effects={second_effects!r}"
    )
