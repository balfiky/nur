"""Engine state persistence — save/load modulator snapshots as JSON."""

from __future__ import annotations

import json
import os
import time

from core.types import UnresolvedItem

ConversationHistory = list[dict[str, str]]


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


def save_conversation_history(history_path: str, history: ConversationHistory) -> None:
    """Persist the hot conversation transcript atomically."""
    payload = {
        "history": [
            {"role": item["role"], "content": item["content"]}
            for item in _clean_history(history)
        ],
        "saved_at": time.time(),
    }
    tmp = history_path + ".tmp"
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, history_path)


def load_conversation_history(history_path: str) -> ConversationHistory:
    """Load a previously persisted hot transcript.

    Returns an empty list if no transcript exists or if the file is malformed.
    """
    if not os.path.exists(history_path):
        return []
    try:
        with open(history_path) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    history = payload.get("history") if isinstance(payload, dict) else None
    if not isinstance(history, list):
        return []
    return _clean_history(history)


def delete_conversation_history(history_path: str) -> None:
    """Delete a hot transcript and its temporary file if present."""
    for path in (history_path, history_path + ".tmp"):
        try:
            os.remove(path)
        except (FileNotFoundError, OSError):
            pass


def _clean_history(raw: list[dict] | list[dict[str, str]]) -> ConversationHistory:
    cleaned: ConversationHistory = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role not in {"user", "assistant"} or not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned
