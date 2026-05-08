"""Pluggable budget for autonomous learning actions.

Sprint 5 wires the local (wall-clock + question count) implementation. The
cloud (token-cost) implementation is a placeholder so the cloud-API path
can plug in later without rewiring the trigger / queue logic.

USAGE:
    budget = LocalBudget(max_questions_per_day=3, max_seconds_per_day=1800)
    if budget.can_pursue():
        do_something()
        budget.consume(questions=1, seconds=elapsed)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class LearningBudget(Protocol):
    """Contract for a learning-action budget."""

    def can_pursue(self) -> bool: ...

    def consume(self, *, questions: int = 0, seconds: float = 0.0, tokens: int = 0) -> None: ...

    def remaining(self) -> dict[str, float]: ...

    def reset_period(self) -> None: ...


# ---------------------------------------------------------------------------
# Local: free per call, capped on wall-clock + count
# ---------------------------------------------------------------------------

@dataclass
class LocalBudget:
    """Local-LLM budget. No per-token cost; cap on count and wall-clock.

    Period rolls over once ``period_seconds`` have elapsed since the last
    reset. With period_seconds=86400 this gives a daily cap.
    """

    max_questions_per_day: int = 3
    max_seconds_per_day: float = 1800.0
    period_seconds: float = 86400.0
    _questions_consumed: int = 0
    _seconds_consumed: float = 0.0
    _period_started_at: float = field(default_factory=time.time)

    def _maybe_roll_period(self) -> None:
        elapsed = time.time() - self._period_started_at
        if elapsed >= self.period_seconds:
            self.reset_period()

    def can_pursue(self) -> bool:
        self._maybe_roll_period()
        if self._questions_consumed >= self.max_questions_per_day:
            return False
        if self._seconds_consumed >= self.max_seconds_per_day:
            return False
        return True

    def consume(self, *, questions: int = 0, seconds: float = 0.0, tokens: int = 0) -> None:
        self._maybe_roll_period()
        self._questions_consumed += max(0, int(questions))
        self._seconds_consumed += max(0.0, float(seconds))

    def remaining(self) -> dict[str, float]:
        self._maybe_roll_period()
        return {
            "questions": max(0, self.max_questions_per_day - self._questions_consumed),
            "seconds": max(0.0, self.max_seconds_per_day - self._seconds_consumed),
            "period_remaining_seconds": max(
                0.0,
                self.period_seconds - (time.time() - self._period_started_at),
            ),
        }

    def reset_period(self) -> None:
        self._questions_consumed = 0
        self._seconds_consumed = 0.0
        self._period_started_at = time.time()


# ---------------------------------------------------------------------------
# Cloud: placeholder. Wire when cloud-API path matures.
# ---------------------------------------------------------------------------

@dataclass
class CloudBudget:
    """Token / dollar budget for cloud-API learning actions.

    Not implemented yet. The interface exists so a cloud path can plug in
    without changing the surfacing / queue logic.
    """

    max_tokens_per_day: int = 0
    max_dollars_per_day: float = 0.0

    def can_pursue(self) -> bool:
        raise NotImplementedError("CloudBudget not implemented yet.")

    def consume(self, *, questions: int = 0, seconds: float = 0.0, tokens: int = 0) -> None:
        raise NotImplementedError("CloudBudget not implemented yet.")

    def remaining(self) -> dict[str, float]:
        raise NotImplementedError("CloudBudget not implemented yet.")

    def reset_period(self) -> None:
        raise NotImplementedError("CloudBudget not implemented yet.")


# ---------------------------------------------------------------------------
# Factory from runtime config
# ---------------------------------------------------------------------------

def from_config(learning_config: dict | None) -> LearningBudget:
    """Build a LearningBudget from a config dict.

    Schema:
        learning:
          budget: local
          local:
            max_questions_per_day: 3
            max_seconds_per_day: 1800
    """
    learning_config = learning_config or {}
    kind = str(learning_config.get("budget", "local")).lower()
    if kind == "local":
        local = learning_config.get("local") or {}
        return LocalBudget(
            max_questions_per_day=int(local.get("max_questions_per_day", 3)),
            max_seconds_per_day=float(local.get("max_seconds_per_day", 1800.0)),
        )
    if kind == "cloud":
        cloud = learning_config.get("cloud") or {}
        return CloudBudget(
            max_tokens_per_day=int(cloud.get("max_tokens_per_day", 0)),
            max_dollars_per_day=float(cloud.get("max_dollars_per_day", 0.0)),
        )
    raise ValueError(f"Unknown learning budget kind: {kind!r}")
