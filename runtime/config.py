"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class RuntimeConfig:
    """Configuration for the Jarvis Runtime."""

    # Storage
    data_dir: str = "data"

    # Backpressure
    max_queue_per_user: int = 3
    max_active_sessions: int = 10

    # Session lifecycle
    session_timeout_seconds: float = 1800.0  # 30 minutes

    @property
    def shared_db_path(self) -> str:
        return os.path.join(self.data_dir, "shared", "self_model.db")

    def user_data_dir(self, rel_key: str) -> str:
        """Per-user data directory. rel_key is platform:user_id."""
        safe = rel_key.replace(":", "_")
        return os.path.join(self.data_dir, safe)

    def user_db_path(self, rel_key: str) -> str:
        return os.path.join(self.user_data_dir(rel_key), "nur.db")

    def user_state_path(self, rel_key: str) -> str:
        return os.path.join(self.user_data_dir(rel_key), "engine_state.json")
