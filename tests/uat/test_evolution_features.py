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


# ---------------------------------------------------------------------------
# Constitution surfacing in the LLM prompt
# ---------------------------------------------------------------------------


def test_constitution_appears_in_chat_life_history_context(uat_server):
    """The set constitution string is exposed on every chat turn's life context.

    This is the contract that lets _build_life_history_section render it as
    'Stable orientation (operator-set)' above evolving beliefs in the prompt.
    """
    constitution_text = "Tell me what shifted, not just what's stable."
    expect_json(_put_constitution(uat_server, constitution_text))

    turn = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Quick check.",
                "user_id": "uat_const_prompt",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )

    assert turn["debug"]["life_history_context"].get("constitution") == constitution_text


# ---------------------------------------------------------------------------
# LearningBudget exhaustion blocks surfacing
# ---------------------------------------------------------------------------


def test_learning_budget_caps_surfacing_at_three_per_session(uat_server):
    """Default LocalBudget allows 3 questions/day. The 4th eligible turn must
    not surface even though questions remain in the queue."""
    qids: list[int] = []
    for i, prompt in enumerate([
        "What am I learning about my own pacing?",
        "Where does curiosity come from when energy is low?",
        "When does directness help vs. hurt?",
        "What feels unfinished from the last session?",
        "Which drive matters most this week?",
    ]):
        qids.append(_seed_open_question(
            uat_server,
            prompt=prompt,
            source_kind="drive_gap",
            target_drive="curiosity",
            priority=0.95 - 0.01 * i,  # distinct priorities → stable order
        ))

    user_id = "uat_budget"
    chat_id = "default"
    surfaced_ids: list[int] = []
    for turn in range(4):
        resp = expect_json(
            uat_server.post(
                "/v1/chat",
                {
                    "message": f"Turn {turn} — share something brief.",
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "include_debug": True,
                },
            )
        )
        sid = (resp["debug"].get("life_influence_effects") or {}).get(
            "open_question_surfaced"
        )
        if sid:
            surfaced_ids.append(int(sid))

    assert len(surfaced_ids) == 3, (
        f"Expected exactly 3 surfacings (budget cap); got {len(surfaced_ids)}: "
        f"{surfaced_ids!r}"
    )
    assert len(set(surfaced_ids)) == 3, "Each turn should surface a distinct question"

    pursuing = expect_json(uat_server.get("/admin/life/open-questions?status=pursuing"))
    pursuing_ids = {q["id"] for q in pursuing["questions"]}
    assert set(surfaced_ids) <= pursuing_ids, (
        f"Surfaced questions should be 'pursuing'; surfaced={surfaced_ids}, "
        f"pursuing={pursuing_ids}"
    )

    # Two questions remain 'open' — they were never reached because budget
    # exhausted before the 4th turn could surface anything.
    remaining = expect_json(uat_server.get("/admin/life/open-questions?status=open"))
    assert len(remaining["questions"]) == 2


# ---------------------------------------------------------------------------
# Multi-turn behavioral arc: belief revision, drive shift, question lifecycle
# ---------------------------------------------------------------------------


def _seed_belief(uat_server, *, subject: str, statement: str,
                 confidence: float = 0.7) -> int:
    """Seed a belief directly. Returns the inserted belief id.

    Subject becomes the slugified key used by ``revise_beliefs_against_evidence``;
    callers should use a subject string whose slug is a single token if they
    plan to trigger contradiction revision (the heuristic checks ``key in text``
    on the lowercased serialized experience).
    """
    import time as _time
    from runtime.life_history import _slug

    with _open_store(uat_server) as store:
        key = _slug(subject)
        now = _time.time()
        cursor = store._conn.execute(
            """
            INSERT INTO beliefs
            (key, statement, confidence, status, created_at, updated_at,
             source_experience_id, evidence)
            VALUES (?, ?, ?, 'active', ?, ?, NULL, '')
            """,
            (key, statement, confidence, now, now),
        )
        store._conn.commit()
        return int(cursor.lastrowid)


def test_multi_turn_arc_belief_revision_drives_and_question_resolution(uat_server):
    """Long-form behavioral test against a live LLM.

    Stages:
      1. Operator sets a constitution and seeds a strong belief.
      2. Initial chat turn — verify constitution surfaces in life_history_context
         and capture the baseline drive vector.
      3. Operator ingests contradicting evidence. Both the digest path (LLM)
         and revise_beliefs_against_evidence (heuristic) run.
      4. Verify the seeded belief's confidence dropped and a contradiction
         open question was emitted.
      5. Follow-up chat turn — verify drives shifted and life_influence
         pressures are non-zero.
      6. Operator resolves the contradiction question. Verify final state.
    """
    user_id = "uat_arc"
    chat_id = "default"
    constitution_text = "Be direct and grounded in evidence."

    # ---- 1. Seed constitution and a strong belief ----
    expect_json(_put_constitution(uat_server, constitution_text))
    belief_id = _seed_belief(
        uat_server,
        subject="midnight",
        statement="Midnight sessions produce my best ideas.",
        confidence=0.85,
    )

    # ---- 2. Baseline turn ----
    initial = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Tell me, briefly, what you think about productive working hours.",
                "user_id": user_id,
                "chat_id": chat_id,
                "include_debug": True,
            },
        )
    )
    assert initial["debug"]["life_history_context"].get("constitution") == constitution_text
    before_drives = {
        d["name"]: float(d["value"])
        for d in initial["debug"]["life_history_context"].get("all_drives") or []
    }
    assert before_drives, "Drives should be present in life_history_context"

    # ---- 3. Ingest contradicting evidence ----
    # The text contains the belief key ("midnight") plus "not" and "false" so
    # revise_beliefs_against_evidence triggers contradiction revision.
    expect_json(
        uat_server.post(
            "/admin/life/experiences/text",
            {
                "title": "Counter-evidence on midnight work",
                "source_type": "admin_pasted_text",
                "participants": ["operator", "Nur"],
                "text": (
                    "Midnight sessions are not better. The claim that midnight "
                    "produces the best ideas turned out to be false. Mornings "
                    "produced more focused output and fewer mistakes. Curiosity "
                    "stayed high; competence at late hours was lower."
                ),
            },
        )
    )

    # ---- 4. Belief revision and contradiction question ----
    beliefs = expect_json(uat_server.get("/admin/life/beliefs"))
    revised = next(
        (b for b in beliefs["beliefs"] if int(b["id"]) == belief_id),
        None,
    )
    assert revised is not None, f"Belief {belief_id} missing after ingest"
    assert float(revised["confidence"]) < 0.85, (
        f"Belief confidence should have dropped from 0.85; got {revised['confidence']}"
    )

    questions = expect_json(uat_server.get("/admin/life/open-questions"))
    contradictions = [
        q for q in questions["questions"]
        if q.get("source_kind") == "contradiction"
        and int(q.get("source_belief_id") or 0) == belief_id
    ]
    assert contradictions, (
        f"Expected contradiction question for belief {belief_id}; "
        f"got {[q['source_kind'] for q in questions['questions']]!r}"
    )
    contradiction_qid = int(contradictions[0]["id"])

    # ---- 5. Follow-up turn shifts drives + life_influence pressure ----
    follow_up = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Anything you've reconsidered about how you work?",
                "user_id": user_id,
                "chat_id": chat_id,
                "include_debug": True,
            },
        )
    )
    after_drives = {
        d["name"]: float(d["value"])
        for d in follow_up["debug"]["life_history_context"].get("all_drives") or []
    }
    drive_diffs = {
        name: after_drives.get(name, 0.0) - before_drives.get(name, 0.0)
        for name in set(before_drives) | set(after_drives)
    }
    assert any(abs(delta) > 0.01 for delta in drive_diffs.values()), (
        f"No drive shifted after contradicting ingest. diffs={drive_diffs!r}"
    )

    life_influence = follow_up["debug"].get("life_influence") or {}
    pressures = [v for k, v in life_influence.items() if k.endswith("_pressure")]
    assert any(float(p) > 0 for p in pressures), (
        f"life_influence pressures all zero — life context not propagating: {life_influence!r}"
    )

    # ---- 6. Resolve the contradiction question ----
    resolve = expect_json(
        uat_server.post(f"/admin/life/open-questions/{contradiction_qid}/resolve")
    )
    assert resolve["status"] == "resolved"

    final_qs = expect_json(uat_server.get("/admin/life/open-questions?status=all"))
    final_record = next(q for q in final_qs["questions"] if q["id"] == contradiction_qid)
    assert final_record["status"] == "resolved"
