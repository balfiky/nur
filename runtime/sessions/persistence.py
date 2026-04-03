"""Engine state persistence — save/load modulator snapshots as JSON."""

from __future__ import annotations

import json
import os
import time

from core.types import UnresolvedItem


def save_engine_state(
    state_path: str,
    snapshot: dict[str, float],
    unresolved_items: list[UnresolvedItem] | list[dict] | None = None,
) -> None:
    """Save engine modulator snapshot to a JSON file.

    Writes atomically (tmp + rename) to avoid corruption on crash.
    """
    state = {
        "modulator_snapshot": snapshot,
        "saved_at": time.time(),
    }
    if unresolved_items is not None:
        serialized: list[dict] = []
        for item in unresolved_items:
            if isinstance(item, UnresolvedItem):
                serialized.append(item.to_dict())
            else:
                serialized.append(dict(item))
        state["unresolved_items"] = serialized
    tmp = state_path + ".tmp"
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)


def load_engine_state(state_path: str) -> dict | None:
    """Load a previously saved engine state.

    Returns None if the file doesn't exist.
    Returns dict with keys: modulator_snapshot, saved_at.
    """
    if not os.path.exists(state_path):
        return None
    with open(state_path) as f:
        data = json.load(f)
    data.setdefault("unresolved_items", [])
    return data
