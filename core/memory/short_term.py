"""Short-term emotional memory.

In-memory list of emotional reactions within a single conversation.
High intensity, rapid access, cleared between sessions.

No LLM calls. No persistence.
"""

from __future__ import annotations

from core.types import EmotionalEvent, ModulatorState, ShortTermEntry


class ShortTermMemory:
    """Stores emotional reactions for the current session."""

    def __init__(self, max_entries: int = 200) -> None:
        self._entries: list[ShortTermEntry] = []
        self._max_entries = max_entries

    def record(self, event: EmotionalEvent, snapshot: ModulatorState) -> ShortTermEntry:
        """Record an emotional reaction. Returns the entry created."""
        entry = ShortTermEntry(
            timestamp=event.timestamp,
            event=event,
            emotion_snapshot=snapshot.copy(),
        )
        self._entries.append(entry)
        # Evict oldest if over capacity
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]
        return entry

    def recent(self, n: int = 10) -> list[ShortTermEntry]:
        """Return the N most recent entries."""
        return self._entries[-n:]

    def all(self) -> list[ShortTermEntry]:
        """Return all entries in chronological order."""
        return list(self._entries)

    def emotional_arc(self) -> list[dict[str, float]]:
        """Return the sequence of modulator snapshots — the session's emotional trajectory."""
        return [
            {"timestamp": e.timestamp, **e.emotion_snapshot.to_dict()}
            for e in self._entries
        ]

    def average_intensity(self) -> float:
        """Mean event intensity across the session. Used by digestion."""
        if not self._entries:
            return 0.0
        return sum(e.event.intensity for e in self._entries) / len(self._entries)

    def peak_intensity(self) -> float:
        """Highest event intensity in the session."""
        if not self._entries:
            return 0.0
        return max(e.event.intensity for e in self._entries)

    def valence_drift(self) -> float:
        """Net valence change from first to last entry. Positive = session improved mood."""
        if len(self._entries) < 2:
            return 0.0
        first = self._entries[0].emotion_snapshot.valence
        last = self._entries[-1].emotion_snapshot.valence
        return last - first

    def clear(self) -> None:
        """Clear all entries. Called at session end after digestion."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
