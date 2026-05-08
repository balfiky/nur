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


# ---------------------------------------------------------------------------
# Metabolism: actual decay, theme promotion, drive_gap & low_confidence emission
# ---------------------------------------------------------------------------


def _force_metabolism_elapsed(uat_server, *, days_ago: float) -> None:
    """Push last_decay_at into the past so the next tick fires real decay."""
    import time as _time
    with _open_store(uat_server) as store:
        store._conn.execute(
            "UPDATE metabolism_state SET last_decay_at = ? WHERE id = 1",
            (_time.time() - days_ago * 86400.0,),
        )
        store._conn.commit()


def test_metabolism_decay_drops_belief_confidence_and_revokes_weak_ones(uat_server):
    """Force elapsed > 1 day, verify beliefs decay per the half-life formula
    (30-day half-life) and that any beliefs falling below 0.2 confidence get
    flipped from 'active' to 'revoked'."""
    strong = _seed_belief(
        uat_server, subject="strong", statement="Strong claim.", confidence=0.9,
    )
    medium = _seed_belief(
        uat_server, subject="medium", statement="Medium claim.", confidence=0.6,
    )
    weak = _seed_belief(
        uat_server, subject="weak", statement="Already-weak claim.", confidence=0.22,
    )

    # 30 days = one half-life; weak (0.22) → ~0.11 < 0.2 → revoked.
    _force_metabolism_elapsed(uat_server, days_ago=30.0)

    tick = expect_json(uat_server.post("/admin/life/metabolism/tick"))
    assert tick["decayed"] is True
    decay_counts = tick["result"]
    assert decay_counts["beliefs"] >= 3
    assert decay_counts["revoked_beliefs"] >= 1

    beliefs = expect_json(uat_server.get("/admin/life/beliefs"))["beliefs"]
    by_id = {int(b["id"]): b for b in beliefs}
    assert by_id[strong]["confidence"] < 0.9
    assert by_id[medium]["confidence"] < 0.6
    assert by_id[weak]["status"] == "revoked"

    # Second tick within the day is rate-limited (no double decay).
    second = expect_json(uat_server.post("/admin/life/metabolism/tick"))
    assert second["decayed"] is False


def test_metabolism_promotes_strong_recurring_theme_to_belief(uat_server):
    """A theme with reinforcement_count >= 5 and accrued_weight >= 0.7 must
    promote into the beliefs table on the next metabolism tick."""
    import time as _time

    signature = "operator:prefers-grounded-evidence"
    expected_key = "operator"  # _safe_label(signature.split(":", 1)[0])

    with _open_store(uat_server) as store:
        now = _time.time()
        store._conn.execute(
            """
            INSERT INTO theme_signatures
            (signature, first_seen, last_seen, reinforcement_count,
             accrued_weight, conflicting_count)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (signature, now - 7 * 86400, now, 6, 0.85),
        )
        store._conn.commit()
        # No belief exists with this key yet.
        existing = store._conn.execute(
            "SELECT 1 FROM beliefs WHERE key = ?", (expected_key,),
        ).fetchone()
        assert existing is None

    _force_metabolism_elapsed(uat_server, days_ago=2.0)

    tick = expect_json(uat_server.post("/admin/life/metabolism/tick"))
    assert tick["decayed"] is True
    consolidation = tick.get("consolidation") or {}
    assert consolidation.get("promoted", 0) >= 1

    beliefs = expect_json(uat_server.get("/admin/life/beliefs"))["beliefs"]
    promoted = next(
        (b for b in beliefs if b.get("key") == expected_key),
        None,
    )
    assert promoted is not None, (
        f"Expected promoted belief with key={expected_key!r}; got {[b.get('key') for b in beliefs]!r}"
    )
    assert promoted["status"] == "active"


def test_metabolism_emits_drive_gap_and_low_confidence_questions(uat_server):
    """Reflection (consolidate_themes) must emit:

    - low_confidence questions for themes with reinforcement >=3 and
      accrued_weight in [0.3, 0.7)
    - drive_gap questions for drives at least 0.2 below baseline
    """
    import time as _time

    weak_theme = "operator:keeps-asking-about-pacing"
    with _open_store(uat_server) as store:
        now = _time.time()
        store._conn.execute(
            """
            INSERT INTO theme_signatures
            (signature, first_seen, last_seen, reinforcement_count,
             accrued_weight, conflicting_count)
            VALUES (?, ?, ?, 4, 0.5, 0)
            """,
            (weak_theme, now - 3 * 86400, now),
        )
        # Drop a drive well below baseline (0.5) — gap >= 0.2 triggers emission.
        store._conn.execute(
            "UPDATE drive_states SET value = ?, updated_at = ? WHERE name = ?",
            (0.20, now, "curiosity"),
        )
        store._conn.commit()

    _force_metabolism_elapsed(uat_server, days_ago=2.0)
    tick = expect_json(uat_server.post("/admin/life/metabolism/tick"))
    assert tick["decayed"] is True

    questions = expect_json(uat_server.get("/admin/life/open-questions"))["questions"]
    sources = {q["source_kind"] for q in questions}
    assert "low_confidence" in sources, (
        f"low_confidence emission missing; sources={sources!r}"
    )
    assert "drive_gap" in sources, (
        f"drive_gap emission missing; sources={sources!r}"
    )
    drive_gap_q = next(q for q in questions if q["source_kind"] == "drive_gap")
    assert drive_gap_q["target_drive"] == "curiosity"
    assert "below baseline" in drive_gap_q["prompt_text"]


# ---------------------------------------------------------------------------
# Skill → life migration (Sprint 4)
# ---------------------------------------------------------------------------


_DISPOSITIONAL_SKILL = """---
name: uat-be-grounded
description: Always prefer evidence over speculation in answers.
---

When asked anything, lean on observed facts rather than imagined ones.
Refuse to fabricate detail that the operator didn't provide.
"""


def test_skill_migration_to_life_history_marks_migrated_and_seeds_experience(uat_server):
    """``runtime.skills.migrate_skill_to_life`` should:

    - mark the original skill ``status='migrated'`` and disabled
    - create a life-history experience with ``source_type='operator_directive'``
    - keep the migrated skill out of ``enabled_skill_context``
    """
    from runtime.config import RuntimeConfig
    from runtime.skills import enabled_skill_context, migrate_skill_to_life

    imported = expect_json(
        uat_server.post(
            "/admin/skills/import",
            {"skill_markdown": _DISPOSITIONAL_SKILL, "name_hint": "uat-be-grounded"},
        )
    )["skill"]
    expect_json(uat_server.post(f"/admin/skills/{imported['id']}/enable"))

    # Sanity: skill is enabled and present in context before migration.
    config = RuntimeConfig.from_yaml(str(uat_server.config_path))
    pre_ctx = enabled_skill_context(config)
    assert any(s["id"] == imported["id"] for s in pre_ctx["skills"])

    life_before = expect_json(uat_server.get("/admin/life"))
    operator_directives_before = sum(
        1 for exp in life_before.get("recent_experiences") or []
        if exp.get("source_type") == "operator_directive"
    )

    result = migrate_skill_to_life(config, imported["id"])
    assert result["status"] == "migrated"
    assert result["id"] == imported["id"]
    assert result["experience"]["source_type"] == "operator_directive"

    # Skill record reflects migration in /admin/skills.
    skills_after = expect_json(uat_server.get("/admin/skills"))["skills"]
    record = next(s for s in skills_after if s["id"] == imported["id"])
    assert record["status"] == "migrated"
    assert record["enabled"] is False

    # Migrated skill is no longer in enabled context.
    post_ctx = enabled_skill_context(config)
    assert not any(s["id"] == imported["id"] for s in post_ctx["skills"])

    # Re-importing migration is idempotent error: already migrated.
    from runtime.skills import SkillError
    with pytest.raises(SkillError):
        migrate_skill_to_life(config, imported["id"])

    # Life history shows a new operator_directive experience.
    life_after = expect_json(uat_server.get("/admin/life"))
    operator_directives_after = sum(
        1 for exp in life_after.get("recent_experiences") or []
        if exp.get("source_type") == "operator_directive"
    )
    assert operator_directives_after > operator_directives_before


# ---------------------------------------------------------------------------
# Long extended chat arc — structural drift over many turns
# ---------------------------------------------------------------------------


def test_extended_chat_arc_demonstrates_structural_drift(uat_server):
    """Run a 10-turn conversation around a coherent theme and verify the
    chat-layer state actually moves: modulators drift away from baseline,
    semantic memories accumulate, the topic profile is built, and the person
    profile records repeated interactions.

    Notes on what's tested vs. not:
      - Life-history drives do NOT shift via chat (only via life ingest), so
        we don't assert on them. Same for the constitution — we just verify
        it's still surfacing on turn 10.
      - Assertions check structural shape, never specific generated text, so
        the test is robust against LLM non-determinism.
    """
    user_id = "uat_long_arc"
    chat_id = "default"

    import requests

    def _slow_chat(message: str) -> dict:
        """UAT helper for chat calls that may take >30s on a live LLM."""
        res = requests.post(
            uat_server.base_url + "/v1/chat",
            json={
                "message": message,
                "user_id": user_id,
                "chat_id": chat_id,
                "include_debug": True,
            },
            timeout=180,
        )
        return expect_json(res)

    expect_json(_put_constitution(
        uat_server,
        "Be grounded; check assumptions before acting.",
    ))

    messages = [
        "I want to talk through how I make decisions under fatigue. Be brief.",
        "Sometimes I push through anyway, even when I know I shouldn't.",
        "What does that say about my relationship with rest?",
        "I think I treat rest as something I have to earn.",
        "Yet the work I do tired is rarely my best.",
        "If I rested more, would I trust the rest itself?",
        "I want to learn to stop earlier without guilt.",
        "What's a small experiment we could agree on?",
        "Ok, let's say I stop at 9pm tonight regardless of progress.",
        "Thanks. Reflect briefly on what you noticed across this conversation.",
    ]

    initial = _slow_chat(messages[0])
    initial_modulators = dict(initial["debug"].get("modulator_snapshot") or {})
    initial_memory_count = len(initial["debug"].get("semantic_memories") or [])

    last_debug = None
    for msg in messages[1:]:
        resp = _slow_chat(msg)
        last_debug = resp["debug"]
        assert resp["response"], "Empty response on a turn"

    assert last_debug is not None

    # 1. Modulator snapshot drifted — at least one core modulator moved.
    final_modulators = dict(last_debug.get("modulator_snapshot") or {})
    moved_modulators = {
        k: float(final_modulators.get(k, 0.0)) - float(initial_modulators.get(k, 0.0))
        for k in set(initial_modulators) | set(final_modulators)
        if isinstance(final_modulators.get(k), (int, float))
        and isinstance(initial_modulators.get(k), (int, float))
    }
    assert any(abs(delta) > 0.01 for delta in moved_modulators.values()), (
        f"No modulator drift across 10 turns: {moved_modulators!r}"
    )

    # 2. Semantic memory grew.
    final_memories = last_debug.get("semantic_memories") or []
    assert len(final_memories) > initial_memory_count, (
        f"Semantic memory did not grow over 10 turns: "
        f"{initial_memory_count} -> {len(final_memories)}"
    )

    # 3. Person profile recorded interactions for this user.
    person_profile = last_debug.get("person_profile") or {}
    interactions = int(person_profile.get("interaction_count") or 0)
    assert interactions >= 9, (
        f"Person profile didn't accumulate interactions across 10 turns: {interactions}"
    )

    # 4. Constitution still surfaces on turn 10 (state not lost across arc).
    assert (
        last_debug["life_history_context"].get("constitution")
        == "Be grounded; check assumptions before acting."
    )


# ---------------------------------------------------------------------------
# Public-release surfaces: bearer auth, backups, soul flow, audit warnings
# ---------------------------------------------------------------------------


def test_bearer_auth_enforced_on_admin_when_api_key_set(uat_server):
    """When ``api_key`` is configured, every /admin/* endpoint must reject
    unauthenticated requests with 401 and accept the configured bearer."""
    import requests

    secret = "uat-public-release-token-77"
    expect_json(uat_server.post("/admin/config", {"api_key": secret}))
    uat_server.restart()

    # Without bearer → 401.
    no_auth = requests.get(uat_server.base_url + "/admin/status", timeout=10)
    assert no_auth.status_code == 401

    # Wrong bearer → 401.
    wrong = requests.get(
        uat_server.base_url + "/admin/status",
        headers={"Authorization": "Bearer wrong"},
        timeout=10,
    )
    assert wrong.status_code == 401

    # Correct bearer → 200.
    ok = requests.get(
        uat_server.base_url + "/admin/status",
        headers={"Authorization": f"Bearer {secret}"},
        timeout=10,
    )
    assert ok.status_code == 200

    # Public health stays open.
    health = requests.get(uat_server.base_url + "/v1/health", timeout=5)
    assert health.status_code == 200


def test_backup_create_list_delete_round_trip(uat_server):
    """Operator should be able to create a backup, see it, and delete it
    only with the typed-confirmation guard."""
    created = expect_json(uat_server.post("/admin/backup", {"include_data": True}))
    filename = created.get("filename") or created.get("path", "").rsplit("/", 1)[-1]
    assert filename, f"backup did not return a filename: {created!r}"

    listing = expect_json(uat_server.get("/admin/backups"))
    backup_files = [b.get("filename") or b.get("name") for b in listing.get("backups", [])]
    assert filename in backup_files, (
        f"Created backup {filename!r} not in listing: {backup_files!r}"
    )

    # Without correct confirmation token → 4xx.
    bad = uat_server.post(
        "/admin/backups/delete",
        {"filename": filename, "confirmation": "wrong"},
    )
    assert bad.status_code >= 400

    # With correct confirmation → success.
    confirmation = f"DELETE {filename}"
    deleted = expect_json(uat_server.post(
        "/admin/backups/delete",
        {"filename": filename, "confirmation": confirmation},
    ))
    assert deleted.get("ok", False) is True

    after = expect_json(uat_server.get("/admin/backups"))
    after_files = [b.get("filename") or b.get("name") for b in after.get("backups", [])]
    assert filename not in after_files


def test_soul_get_update_round_trip(uat_server):
    """The /admin/soul GET/POST cycle should validate, persist, and reload
    the soul without requiring a process restart."""
    initial = expect_json(uat_server.get("/admin/soul"))
    assert initial.get("saved") is True
    initial_soul = initial.get("soul") or {}
    assert "name" in initial_soul

    update_payload = {
        "name": "UAT Persona",
        "identity": "An evidence-grounded research partner.",
        "voice": "Calm and direct.",
        "relational_stance": "Collaborator with sharp opinions.",
        "growth_policy": "Update beliefs only when evidence demands it.",
        "likes": ["clarity", "small experiments"],
        "dislikes": ["unverified claims"],
        "boundaries": ["no fabricated facts"],
        "core_values": {"honesty": 0.95, "kindness": 0.7},
        "initial_traits": {"discipline": 0.8},
    }
    saved = expect_json(uat_server.post("/admin/soul", update_payload))
    assert saved.get("saved") is True

    after = expect_json(uat_server.get("/admin/soul"))
    after_soul = after.get("soul") or {}
    assert after_soul.get("name") == "UAT Persona"
    assert "evidence-grounded" in (after_soul.get("identity") or "")
    assert "clarity" in (after_soul.get("likes") or [])

    # Invalid payload (blank name) → 422.
    bad = uat_server.post(
        "/admin/soul",
        {**update_payload, "name": "   "},
    )
    assert bad.status_code == 422


_NO_TRIGGER_SKILL = """---
name: uat-always-on
description: A skill with no applies_when — should warn at audit time.
---

This skill has no trigger condition so it would load every turn.
"""


def test_skill_audit_warns_when_applies_when_missing(uat_server):
    """A skill imported without ``applies_when`` must still import (not error)
    but the audit must surface a warning so operators can fix it."""
    imported = expect_json(
        uat_server.post(
            "/admin/skills/import",
            {"skill_markdown": _NO_TRIGGER_SKILL, "name_hint": "uat-always-on"},
        )
    )["skill"]
    warnings = imported.get("compatibility", {}).get("warnings") or []
    assert any("applies_when" in w for w in warnings), (
        f"Expected applies_when warning at import; got {warnings!r}"
    )

    skills_payload = expect_json(uat_server.get("/admin/skills"))
    record = next(s for s in skills_payload["skills"] if s["id"] == imported["id"])
    record_warnings = record.get("compatibility", {}).get("warnings") or []
    assert any("applies_when" in w for w in record_warnings)
    # The skill should still be importable (status=needs_review, not erroring).
    assert record["status"] == "needs_review"
    assert record.get("compatibility", {}).get("compatible") is True


# ---------------------------------------------------------------------------
# Diagnostics, sessions reset, soul draft, file-path skill import, CORS
# ---------------------------------------------------------------------------


def test_admin_diagnostics_returns_expected_shape(uat_server):
    payload = expect_json(uat_server.get("/admin/diagnostics"))
    assert payload["status"] == "ok"
    assert "generated_at" in payload
    server = payload.get("server") or {}
    assert isinstance(server.get("pid"), int) and server["pid"] > 0
    assert server.get("python")
    assert isinstance(server.get("uptime_seconds"), (int, float))
    config_block = payload.get("config") or {}
    assert config_block["llm_backend"] == "codex"
    assert config_block["auth_enabled"] is False  # no api_key set in fixture
    runtime_block = payload.get("runtime") or {}
    assert isinstance(runtime_block["active_sessions"], int)
    assert runtime_block["active_sessions"] >= 0
    storage = payload.get("storage") or {}
    assert "data_dir" in storage
    assert isinstance(payload.get("warnings"), list)


def test_admin_sessions_reset_evicts_active_session(uat_server):
    """Operator must be able to evict a runaway session with typed
    confirmation; wrong confirmation must 400; unknown session 404."""
    user_id = "uat_session_reset"
    chat_id = "default"
    session_key = f"web:{user_id}:{chat_id}"

    # Create the session by sending a chat turn.
    expect_json(uat_server.post("/v1/chat", {
        "message": "Hello, please respond briefly.",
        "user_id": user_id,
        "chat_id": chat_id,
    }))

    diag_before = expect_json(uat_server.get("/admin/diagnostics"))
    assert diag_before["runtime"]["active_sessions"] >= 1

    # Wrong confirmation → 400.
    bad = uat_server.post(
        "/admin/sessions/reset",
        {"session_key": session_key, "confirmation": "RESET wrong"},
    )
    assert bad.status_code == 400

    # Unknown session_key with right-shape confirmation → 404.
    unknown = uat_server.post(
        "/admin/sessions/reset",
        {"session_key": "web:nobody:nada", "confirmation": "RESET web:nobody:nada"},
    )
    assert unknown.status_code == 404

    # Correct confirmation → eviction.
    ok_payload = expect_json(uat_server.post(
        "/admin/sessions/reset",
        {"session_key": session_key, "confirmation": f"RESET {session_key}"},
    ))
    assert ok_payload["status"] == "evicted"
    assert ok_payload["session_key"] == session_key

    diag_after = expect_json(uat_server.get("/admin/diagnostics"))
    # The session that was active is now gone.
    assert diag_after["runtime"]["active_sessions"] < diag_before["runtime"]["active_sessions"]


def test_admin_soul_draft_returns_validated_schema_from_llm(uat_server):
    """LLM-assisted soul drafting must:
    - require a non-trivial description (>=8 chars after strip)
    - call the LLM with the soul_from_description prompt template
    - return a draft that parses as a valid AdminSoulUpdateRequest schema
    - explicitly NOT persist (operator clicks Save Identity to commit)
    """
    import requests

    # Too-short description → 422.
    too_short = uat_server.post("/admin/soul/draft", {"description": "hi"})
    assert too_short.status_code == 422

    # Real description → live LLM call (slow with codex).
    res = requests.post(
        uat_server.base_url + "/admin/soul/draft",
        json={
            "description": (
                "A grounded coding partner who pushes back when assumptions "
                "look weak and prefers to ask one clarifying question over "
                "guessing. Voice: dry, concise. Boundaries: no fabrication."
            ),
        },
        timeout=180,
    )
    payload = expect_json(res)
    assert payload["ok"] is True
    assert payload["backend"] == "codex"
    draft = payload.get("draft") or {}
    # Required schema fields.
    assert isinstance(draft.get("name"), str) and draft["name"].strip()
    for field in (
        "identity", "voice", "relational_stance", "growth_policy",
    ):
        assert field in draft
    assert isinstance(draft.get("likes"), list)
    assert isinstance(draft.get("dislikes"), list)
    assert isinstance(draft.get("boundaries"), list)
    # Weights bounded.
    for trait, weight in (draft.get("core_values") or {}).items():
        assert 0.0 <= float(weight) <= 1.0, f"Out-of-bounds value for {trait}: {weight}"
    for trait, weight in (draft.get("initial_traits") or {}).items():
        assert 0.0 <= float(weight) <= 1.0, f"Out-of-bounds trait for {trait}: {weight}"

    # The draft must NOT have been persisted.
    current = expect_json(uat_server.get("/admin/soul"))
    assert current["soul"].get("name") != draft["name"], (
        "Soul draft should not auto-persist; operator must click Save Identity."
    )


def test_skill_import_via_file_path_validates_input(uat_server, tmp_path):
    """Filesystem-path skill import must:
    - succeed for a valid skill folder
    - reject a path that doesn't exist
    - reject a directory that has no SKILL.md
    """
    # Valid: a real skill folder with SKILL.md.
    valid_root = tmp_path / "uat-fs-skill"
    valid_root.mkdir()
    (valid_root / "SKILL.md").write_text(
        "---\n"
        "name: uat-fs-skill\n"
        "description: A simple UAT skill imported by file path.\n"
        "applies_when: filesystem\n"
        "---\n\n"
        "Body of the skill.\n",
        encoding="utf-8",
    )
    imported = expect_json(uat_server.post(
        "/admin/skills/import",
        {"source_path": str(valid_root), "name_hint": "uat-fs-skill"},
    ))["skill"]
    assert imported["id"] == "uat-fs-skill"
    assert imported["status"] == "needs_review"

    # Non-existent path → 4xx.
    missing = uat_server.post(
        "/admin/skills/import",
        {"source_path": str(tmp_path / "does-not-exist")},
    )
    assert missing.status_code >= 400
    detail = (missing.json().get("detail") or "").lower()
    assert "exist" in detail or "not found" in detail

    # Directory without SKILL.md → 4xx.
    empty_dir = tmp_path / "no-skill-md"
    empty_dir.mkdir()
    (empty_dir / "README.md").write_text("not a skill", encoding="utf-8")
    no_md = uat_server.post(
        "/admin/skills/import",
        {"source_path": str(empty_dir)},
    )
    assert no_md.status_code >= 400

    # Both fields filled → rejected (must pick one).
    both = uat_server.post(
        "/admin/skills/import",
        {"source_path": str(valid_root), "skill_markdown": "---\nname: x\n---\n"},
    )
    assert both.status_code >= 400


def test_cors_origins_config_is_respected_at_startup(uat_server):
    """``cors_origins`` is read at app startup. Setting it via /admin/config
    + restart should make the running server emit Access-Control-Allow-Origin
    only for the configured origin and not for unrelated ones."""
    import requests

    allowed = "https://nur-allowed.example.com"
    expect_json(uat_server.post(
        "/admin/config",
        {"cors_origins": [allowed]},
    ))
    uat_server.restart()

    # Allowed origin → CORS header present and matches.
    allowed_res = requests.get(
        uat_server.base_url + "/v1/health",
        headers={"Origin": allowed},
        timeout=10,
    )
    assert allowed_res.ok
    assert allowed_res.headers.get("access-control-allow-origin") == allowed

    # Disallowed origin → no CORS allow header (browser would block).
    blocked_res = requests.get(
        uat_server.base_url + "/v1/health",
        headers={"Origin": "https://evil.example.com"},
        timeout=10,
    )
    assert blocked_res.ok  # Server still answers; browser enforces CORS.
    assert blocked_res.headers.get("access-control-allow-origin") in (None, "")


# ---------------------------------------------------------------------------
# Comprehensive aggregator — single PASS/FAIL for the entire UAT suite
# ---------------------------------------------------------------------------


@pytest.mark.comprehensive
def test_complete_release_uat_suite():
    """Single aggregator that runs every other UAT test in a subprocess and
    asserts they all pass.

    This is THE test for "is the whole product green?" — one assertion, one
    exit code, every other UAT scenario covered. Uses a subprocess so each
    underlying test still gets its own clean fixtures (fresh DB, fresh
    process, isolated state) — no cross-test contamination.

    Run it explicitly with:
        NUR_UAT_LIVE=1 NUR_UAT_BACKEND=codex \\
          python -m pytest tests/uat/ -m comprehensive -v

    Or via the Makefile shortcut:
        make uat

    The ``-m "uat and not comprehensive"`` filter inside avoids recursion.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/uat/",
        "--tb=short",
        "-m", "uat and not comprehensive",
        "-q",
    ]
    env = os.environ.copy()
    # Forward live-mode flags.
    if "NUR_UAT_LIVE" not in env:
        pytest.skip(
            "Comprehensive aggregator requires NUR_UAT_LIVE=1 (live backend).",
        )
    result = subprocess.run(
        cmd, cwd=str(repo_root), env=env, capture_output=False, check=False,
    )
    assert result.returncode == 0, (
        f"Comprehensive UAT suite had failures (subprocess exit {result.returncode})."
    )
