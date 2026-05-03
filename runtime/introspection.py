"""Self-reflection records for Life History."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
import time
from typing import Any

from runtime.config import RuntimeConfig
from runtime.life_history import LifeHistoryStore


@dataclass(frozen=True)
class IntrospectionEvent:
    trigger_turn_id: str
    dialogue_trace: dict[str, Any]
    conclusion: str = ""
    unresolved_residue: str = ""
    intensity: float = 0.0
    created_at: float = field(default_factory=time.time)


def record_introspection(
    config: RuntimeConfig,
    event: IntrospectionEvent,
) -> dict[str, Any]:
    """Record self-reflection through the Learning channel pipeline."""
    text = (
        f"Self-reflection trigger: {event.trigger_turn_id}\n"
        f"Conclusion: {event.conclusion}\n"
        f"Unresolved residue: {event.unresolved_residue}\n"
        f"Trace: {event.dialogue_trace}"
    )
    with LifeHistoryStore(config) as store:
        return store.ingest_external_text(
            title="Self-reflection",
            text=text,
            source_type="self_reflection",
            source_ref=event.trigger_turn_id,
            participants=["Nūr"],
            metadata={"introspection": asdict(event)},
        )
