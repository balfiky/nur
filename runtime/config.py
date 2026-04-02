"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields as dc_fields


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

    # Channels
    console_enabled: bool = True

    # Telegram
    telegram_token: str = ""
    telegram_allowlist: set[str] = field(default_factory=set)
    telegram_poll_timeout: int = 30
    dedupe_ttl: float = 60.0

    # LLM backend
    llm_backend: str = "auto"      # "auto", "minimax", "mock"
    minimax_api_key: str = ""      # overridden by MINIMAX_API_KEY env var if empty

    # Debug API
    debug_host: str = "127.0.0.1"
    debug_port: int = 8077

    @classmethod
    def from_yaml(cls, path: str) -> RuntimeConfig:
        """Load config from a YAML file.  Missing keys use defaults."""
        import yaml

        if not os.path.exists(path):
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        # Convert list → set for allowlist
        al = data.pop("telegram_allowlist", None)
        if al is not None:
            data["telegram_allowlist"] = {str(x) for x in al}
        # Drop unknown keys so __init__ doesn't blow up
        known = {f.name for f in dc_fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @property
    def shared_db_path(self) -> str:
        return os.path.join(self.data_dir, "shared", "self_model.db")

    def user_data_dir(self, rel_key: str) -> str:
        """Per-user data directory.  rel_key is platform:user_id."""
        safe = rel_key.replace(":", "_")
        return os.path.join(self.data_dir, safe)

    def user_db_path(self, rel_key: str) -> str:
        return os.path.join(self.user_data_dir(rel_key), "nur.db")

    def user_state_path(self, rel_key: str) -> str:
        return os.path.join(self.user_data_dir(rel_key), "engine_state.json")
