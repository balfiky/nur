"""Engine state persistence — save/load modulator snapshots as JSON."""

from __future__ import annotations

import json
import os
import time


def save_engine_state(state_path: str, snapshot: dict[str, float]) -> None:
    """Save engine modulator snapshot to a JSON file.

    Writes atomically (tmp + rename) to avoid corruption on crash.
    """
    state = {
        "modulator_snapshot": snapshot,
        "saved_at": time.time(),
    }
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
        return json.load(f)
