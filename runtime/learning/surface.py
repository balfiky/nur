"""Drive-triggered surfacing of open questions during conversation.

Sprint 5 stage 1.5. The mechanic:

  1. Drive thresholds gate "now": only surface a question whose target_drive
     is currently above its baseline (Nur is "moved to ask" about it).
  2. Budget gates "how often": ``LearningBudget.can_pursue()`` must return
     True before any surfacing happens.
  3. Throttle: ``max_per_session`` ensures Nur doesn't pile multiple
     follow-up questions onto a single response.

When ``should_surface_question`` returns a non-None question, the caller
should mark it pursuing, append a graceful follow-up to the response, and
consume the budget.
"""

from __future__ import annotations

from typing import Any

from runtime.learning_budget import LearningBudget
from runtime.life_history import LifeHistoryStore


DEFAULT_DRIVE_THRESHOLD = 0.55  # current value must exceed baseline + 0.05


def should_surface_question(
    store: LifeHistoryStore,
    *,
    budget: LearningBudget | None = None,
    drive_threshold: float = DEFAULT_DRIVE_THRESHOLD,
    max_priority_pool: int = 5,
) -> dict[str, Any] | None:
    """Pick an open question to surface, or return None.

    Selection rule: among the top-priority open questions, prefer those
    whose target_drive is currently above ``drive_threshold``. Tie-break by
    priority (descending) and creation time (oldest first within tier).

    Drive_gap questions whose target_drive is BELOW baseline are still
    eligible, since the gap is exactly what makes them salient.
    """
    if budget is not None and not budget.can_pursue():
        return None

    questions = store.list_open_questions(status="open", limit=max_priority_pool)
    if not questions:
        return None

    drives = {drive["name"]: float(drive["value"]) for drive in store.list_drives()}

    # Tier 1: question whose target_drive is currently elevated (Nur is
    # actively moved to ask about it). Drive_gap questions are kept regardless
    # of drive level — the gap itself is the signal.
    elevated: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for q in questions:
        target = q.get("target_drive")
        if q.get("source_kind") == "drive_gap":
            elevated.append(q)
            continue
        if target and drives.get(target, 0.0) >= drive_threshold:
            elevated.append(q)
            continue
        fallback.append(q)

    pool = elevated if elevated else fallback
    if not pool:
        return None
    # list_open_questions already sorts by priority desc + created_at asc.
    return pool[0]
