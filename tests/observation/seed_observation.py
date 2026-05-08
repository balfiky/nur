"""Synthetic Sprint 2 observation: populate a fresh life-history db with
deliberate fixtures, force the metabolism tick, and print the resulting
open-questions queue for human review.

This replaces the 2-week real-use observation period from the original
execution plan with a controlled smoke test. It exercises all three
emission paths (contradiction, low_confidence, drive_gap) so a reviewer
can rate the queue's quality without waiting for natural accumulation.

USAGE:
  python tests/observation/seed_observation.py
    → uses an isolated temp db, prints queue, exits.
  python tests/observation/seed_observation.py --keep
    → keeps the temp db on disk; prints its path.

Decision Gate 2 (manual): of the questions printed, how many would you
write yourself if asked "what does Nur not yet understand?" >= 7/10 of
the top open questions = pass. <= 3/10 = stop and rework digest/emission.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runtime.config import RuntimeConfig  # noqa: E402
from runtime.life_history import LifeHistoryStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — varied experiences across recurring themes, contradiction,
# and emotional polarity. Heuristic digest only (no LLM dependency).
# ---------------------------------------------------------------------------

INGESTION_FIXTURES: list[dict[str, str]] = [
    {
        "title": "Persistence in debugging",
        "text": (
            "Working through a hard race condition took two days of patient "
            "iteration. Persistence and learning from each failed attempt is "
            "what made the practice productive."
        ),
    },
    {
        "title": "Slow thinking",
        "text": (
            "I caught a subtle off-by-one error this morning by slowing down "
            "to think before coding. Practice and learning to question "
            "assumptions matters more than typing speed."
        ),
    },
    {
        "title": "Iterating builds skill",
        "text": (
            "Each iteration through failure builds real competence. Practice "
            "and the willingness to keep learning is what distinguishes a "
            "junior from a senior engineer."
        ),
    },
    {
        "title": "Reading deeply",
        "text": (
            "I read a chapter on consciousness today. The book left me with "
            "more questions than answers about identity and experience."
        ),
    },
    {
        "title": "Repair after rupture",
        "text": (
            "A small relationship rupture this week reminded me that repair "
            "and apology take more sustained effort than initial trust."
        ),
    },
    {
        "title": "Trust matters",
        "text": (
            "Continuity of trust in a relationship turns out to be hard to "
            "rebuild once damaged. Repair work is slow and the attachment "
            "feels different afterward."
        ),
    },
    {
        "title": "Quiet day",
        "text": "Mostly a quiet day. Caught up on email.",
    },
    {
        "title": "Routine standup",
        "text": "Team standup, normal blockers, nothing notable.",
    },
]


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _seed_existing_belief(store: LifeHistoryStore) -> None:
    """Insert a belief that the next ingestion will deliberately contradict."""
    now = time.time()
    store._conn.execute(
        """
        INSERT INTO beliefs
        (key, statement, confidence, status, created_at, updated_at,
         source_experience_id, evidence)
        VALUES ('persistence', 'Persistence always pays off.',
                0.85, 'active', ?, ?, NULL, 'pre-seeded for contradiction')
        """,
        (now, now),
    )
    store._conn.commit()


def _ingest_contradicting_experience(store: LifeHistoryStore) -> None:
    """An experience whose text contains the seeded belief's key + 'not'/'false'."""
    store.ingest_pasted_text(
        title="Reframing persistence",
        text=(
            "Persistence is not always virtuous. Sometimes letting go is "
            "the wiser move; treating pure persistence as false economy "
            "took me years to learn."
        ),
        source_type="admin_pasted_text",
    )


def _push_drives_below_baseline(store: LifeHistoryStore) -> None:
    """Simulate sustained drift so drive_gap survives decay-toward-baseline.

    Drives below baseline get pulled UP by decay (toward 0.5). For a 31-day
    elapsed tick, ~50% of the gap closes. To stay >0.2 below baseline after
    decay we need to start at <=0.05 (0.5 - 0.05 = 0.45 gap, decay halves
    it to ~0.22 — still above the 0.2 emission threshold).
    """
    store._conn.execute(
        "UPDATE drive_states SET value = 0.05 WHERE name = 'curiosity'"
    )
    store._conn.execute(
        "UPDATE drive_states SET value = 0.05 WHERE name = 'caution'"
    )
    store._conn.commit()


def _seed_partial_themes(store: LifeHistoryStore) -> None:
    """Seed theme signatures with partial accumulation to trigger
    low_confidence emission. The heuristic digest's natural weight per
    ingestion is ~0.05-0.15, far too small to reach the [0.3, 0.7) range
    in 8 ingestions; without seeding, low_confidence questions never fire
    in this synthetic window. Real-world accumulation happens over weeks.
    """
    now = time.time()
    seeds = [
        ("reflection:slowness", 4, 0.55),
        ("competence:practice", 5, 0.45),
        ("relational:trust", 3, 0.40),
    ]
    for signature, count, weight in seeds:
        store._conn.execute(
            """
            INSERT INTO theme_signatures
            (signature, first_seen, last_seen, reinforcement_count,
             accrued_weight, conflicting_count)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (signature, now, now, count, weight),
        )
    store._conn.commit()


def _force_metabolism_tick(store: LifeHistoryStore) -> dict:
    """Backdate last_decay_at so wall_clock_decay actually fires."""
    store._conn.execute(
        "UPDATE metabolism_state SET last_decay_at = ? WHERE id = 1",
        (time.time() - (31 * 86400),),
    )
    store._conn.commit()
    return store.wall_clock_decay()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _print_queue(store: LifeHistoryStore) -> None:
    counts = store.count_open_questions()
    questions = store.list_open_questions(status="open", limit=20)

    _print_section("OPEN QUESTIONS QUEUE")
    print(
        f"counts: open={counts['open']} pursuing={counts['pursuing']} "
        f"resolved={counts['resolved']} abandoned={counts['abandoned']}"
    )
    if not questions:
        print()
        print("(queue is empty — emission did not fire)")
        return
    for idx, q in enumerate(questions, 1):
        print()
        print(f"[{idx}] priority={q['priority']:.2f}  source={q['source_kind']}")
        if q.get("target_drive"):
            print(f"    target_drive: {q['target_drive']}")
        if q.get("source_belief_id"):
            print(f"    source_belief_id: {q['source_belief_id']}")
        print(f"    {q['prompt_text']}")


def _print_belief_state(store: LifeHistoryStore) -> None:
    _print_section("RESULTING BELIEF STATE")
    rows = store._conn.execute(
        "SELECT key, confidence, status FROM beliefs ORDER BY confidence DESC"
    ).fetchall()
    if not rows:
        print("(no beliefs)")
        return
    for row in rows:
        print(f"  {row['key']:30s}  conf={float(row['confidence']):.3f}  {row['status']}")


def _print_drive_state(store: LifeHistoryStore) -> None:
    _print_section("RESULTING DRIVE STATE")
    rows = store._conn.execute(
        "SELECT name, value FROM drive_states ORDER BY name"
    ).fetchall()
    for row in rows:
        print(f"  {row['name']:14s}  value={float(row['value']):.3f}")


def _print_theme_signatures(store: LifeHistoryStore) -> None:
    _print_section("THEME SIGNATURES (post-tick)")
    rows = store._conn.execute(
        """
        SELECT signature, reinforcement_count, accrued_weight
        FROM theme_signatures
        ORDER BY accrued_weight DESC, reinforcement_count DESC
        LIMIT 20
        """
    ).fetchall()
    if not rows:
        print("(no theme signatures)")
        return
    for row in rows:
        print(
            f"  {str(row['signature'])[:48]:48s}  "
            f"count={int(row['reinforcement_count']):2d}  "
            f"weight={float(row['accrued_weight']):.3f}"
        )


def _print_decision_gate_prompt() -> None:
    _print_section("DECISION GATE 2 (manual rating)")
    print(
        "For each open question above, ask: 'would I write this question if "
        "asked what Nur does not yet understand?'\n"
        "  >=7 of top 10 = real gaps   → pass; proceed to Sprint 3.\n"
        "  4-6 of 10                   → partial; rework priority/threshold.\n"
        "  <=3 of 10                   → fail; rework digest or emission."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the seeded db on disk; print its path.",
    )
    args = parser.parse_args(argv)

    if args.keep:
        tmp = Path(tempfile.mkdtemp(prefix="nur-observation-"))
    else:
        tmp_obj = tempfile.TemporaryDirectory(prefix="nur-observation-")
        tmp = Path(tmp_obj.name)

    workspace = tmp / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = RuntimeConfig(
        data_dir=str(tmp / "data"),
        tools_workspace=str(workspace),
    )

    print(f"observation db: {config.data_dir}")

    with LifeHistoryStore(config) as store:
        _seed_existing_belief(store)
        _seed_partial_themes(store)
        for fixture in INGESTION_FIXTURES:
            store.ingest_pasted_text(
                title=fixture["title"],
                text=fixture["text"],
                source_type="admin_pasted_text",
            )
        _ingest_contradicting_experience(store)
        _push_drives_below_baseline(store)
        outcome = _force_metabolism_tick(store)

        _print_section("METABOLISM TICK OUTCOME")
        print(f"  decayed:        {outcome.get('decayed')}")
        print(f"  elapsed_days:   {outcome.get('elapsed_days', 0):.2f}")
        print(f"  decay_result:   {outcome.get('result', {})}")
        print(f"  consolidation:  {outcome.get('consolidation', {})}")

        _print_belief_state(store)
        _print_drive_state(store)
        _print_theme_signatures(store)
        _print_queue(store)
        _print_decision_gate_prompt()

    if args.keep:
        print()
        print(f"db kept at: {tmp}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
