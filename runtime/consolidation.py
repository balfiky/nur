"""Metabolic Life History consolidation tasks."""

from __future__ import annotations

from typing import Any

from runtime.config import RuntimeConfig
from runtime.life_history import LifeHistoryStore


def run_consolidation_tick(
    config: RuntimeConfig,
    *,
    elapsed_days: float = 1.0,
) -> dict[str, Any]:
    """Run one deterministic decay/consolidation tick."""
    with LifeHistoryStore(config) as store:
        decay = store.decay_step(elapsed_days=elapsed_days)
        consolidation = store.consolidate_themes()
    return {"decay": decay, "consolidation": consolidation}
