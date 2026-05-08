"""Sprint 5.1 — LearningBudget tests."""

from __future__ import annotations

import time

import pytest

from runtime.learning_budget import (
    CloudBudget,
    LocalBudget,
    from_config,
)


def test_local_budget_starts_with_full_capacity():
    b = LocalBudget(max_questions_per_day=3, max_seconds_per_day=600)
    assert b.can_pursue() is True
    rem = b.remaining()
    assert rem["questions"] == 3
    assert rem["seconds"] == 600


def test_local_budget_consume_questions_decrements_remaining():
    b = LocalBudget(max_questions_per_day=3)
    b.consume(questions=1)
    assert b.remaining()["questions"] == 2
    b.consume(questions=2)
    assert b.remaining()["questions"] == 0


def test_local_budget_blocks_when_question_cap_reached():
    b = LocalBudget(max_questions_per_day=2)
    b.consume(questions=2)
    assert b.can_pursue() is False


def test_local_budget_blocks_when_seconds_cap_reached():
    b = LocalBudget(max_seconds_per_day=10)
    b.consume(seconds=10)
    assert b.can_pursue() is False


def test_local_budget_reset_period_restores_capacity():
    b = LocalBudget(max_questions_per_day=2)
    b.consume(questions=2)
    assert b.can_pursue() is False
    b.reset_period()
    assert b.can_pursue() is True
    assert b.remaining()["questions"] == 2


def test_local_budget_auto_rolls_after_period_elapses():
    b = LocalBudget(max_questions_per_day=1, period_seconds=0.1)
    b.consume(questions=1)
    assert b.can_pursue() is False
    time.sleep(0.15)
    # Next can_pursue triggers an auto-roll.
    assert b.can_pursue() is True


def test_cloud_budget_raises_not_implemented():
    b = CloudBudget()
    with pytest.raises(NotImplementedError):
        b.can_pursue()


def test_from_config_default_returns_local():
    b = from_config(None)
    assert isinstance(b, LocalBudget)


def test_from_config_explicit_local_with_caps():
    b = from_config({
        "budget": "local",
        "local": {"max_questions_per_day": 5, "max_seconds_per_day": 60},
    })
    assert isinstance(b, LocalBudget)
    assert b.max_questions_per_day == 5
    assert b.max_seconds_per_day == 60


def test_from_config_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown learning budget kind"):
        from_config({"budget": "moon"})
