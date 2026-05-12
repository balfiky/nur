"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields as dc_fields


_SECRET_FIELDS = {
    "telegram_token",
    "llm_api_key",
    "api_key",
}
_LEGACY_FIELD_ALIASES = {
    "proactive_max_per_session": "proactive_density_reference",
    "proactive_cooldown": "proactive_recovery_seconds",
}


@dataclass
class RuntimeConfig:
    """Configuration for the Nūr Runtime."""

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

    # LLM backend — Nūr always calls a real model. There is no mock path.
    # Valid: "auto", "provider", "openai_compatible", "codex", "minimax".
    llm_backend: str = "auto"
    llm_base_url: str = ""         # API endpoint for OpenAI-compatible backends
    llm_model: str = ""            # model name for OpenAI-compatible backends
    llm_api_key: str = ""          # generic API key (optional for local endpoints)

    # Debug API
    debug_host: str = "127.0.0.1"
    debug_port: int = 8077

    # Proactive behavior (Phase 8)
    proactive_enabled: bool = False
    proactive_idle_threshold: float = 300.0    # seconds idle before proactive check
    proactive_density_reference: int = 3       # action density reference for recovery pressure
    proactive_recovery_seconds: float = 300.0  # recovery curve after proactive actions
    proactive_check_interval: float = 60.0     # how often the proactive loop runs

    # Character independence runtime
    character_independence: bool = False
    coherence_min_score: float = 0.6
    coherence_max_regenerations: int = 2
    pending_intake_ttl_turns: int = 3

    # Public integration API (standalone web server)
    api_key: str = ""                          # bearer token; empty = auth disabled
    cors_origins: list[str] = field(default_factory=list)  # CORS allowlist; empty = same-origin only

    # Agentic tool runtime
    # Tools are off by default: user-text heuristics can invoke destructive
    # operations (fs.delete_path, shell.run_command, etc.), and the default
    # HTTP surface does not authenticate every request. Opt in explicitly.
    tools_enabled: bool = False
    # How independently the assistant may act once tools are enabled:
    # off | assisted | autonomous | high_risk
    autonomy_level: str = "assisted"
    # Workspace root for filesystem tools when enabled. Empty string means
    # "<data_dir>/workspace". Filesystem tool calls that resolve outside this
    # root are refused.
    tools_workspace: str = ""
    # Shell tool is a separate opt-in even when tools_enabled=True: subprocess
    # execution is a larger blast radius than bounded file I/O.
    shell_tool_enabled: bool = False

    # Learning schedule (Sprint 5). The LearningBudget caps how often Nūr can
    # surface an open question to the user inside chat. Metabolism min elapsed
    # days is the rate limit on the wall-clock reflection tick.
    learning_budget_kind: str = "local"        # "local" | "cloud" (cloud not yet implemented)
    learning_max_questions_per_day: int = 3
    learning_max_seconds_per_day: float = 1800.0
    metabolism_min_elapsed_days: float = 1.0

    # Self-action layer (v0.30). When enabled, the pipeline runs a self-intent
    # proposer between the tool loop and defense stage so Nūr can invoke
    # bounded self.* tools (snapshot, log, note, verify, alert_owner) for its
    # own reasons. owner_chat_id, when set, is the Telegram chat that
    # ``self.alert_owner`` notifies (no-op when equal to the current user).
    self_intent_enabled: bool = True
    max_self_intents_per_turn: int = 3
    owner_chat_id: str = ""

    def learning_budget(self):
        """Build a LearningBudget instance from the configured settings."""
        from runtime.learning_budget import from_config as _budget_from_config
        return _budget_from_config({
            "budget": self.learning_budget_kind,
            "local": {
                "max_questions_per_day": self.learning_max_questions_per_day,
                "max_seconds_per_day": self.learning_max_seconds_per_day,
            },
        })

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
        # Normalize cors_origins to list[str]
        co = data.get("cors_origins")
        if co is not None:
            data["cors_origins"] = [str(x) for x in co]
        for legacy_key, current_key in _LEGACY_FIELD_ALIASES.items():
            if current_key not in data and legacy_key in data:
                data[current_key] = data[legacy_key]
        # Drop unknown keys so __init__ doesn't blow up
        known = {f.name for f in dc_fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_yaml_dict(self) -> dict[str, object]:
        """Serialize to a YAML-friendly dict in dataclass field order."""
        data: dict[str, object] = {}
        for field_def in dc_fields(self):
            value = getattr(self, field_def.name)
            if isinstance(value, set):
                value = sorted(value)
            data[field_def.name] = value
        return data

    def write_yaml(self, path: str) -> None:
        """Persist this config to YAML."""
        import yaml

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(
                self.to_yaml_dict(),
                f,
                sort_keys=False,
                default_flow_style=False,
            )

    def to_public_dict(self) -> dict[str, object]:
        """Serialize for UI/API use without exposing secret values."""
        data = self.to_yaml_dict()
        for field_name in _SECRET_FIELDS:
            data[field_name] = ""
        return data

    def secret_status(self) -> dict[str, bool]:
        """Return whether each secret-like field is configured."""
        return {
            field_name: bool(getattr(self, field_name))
            for field_name in sorted(_SECRET_FIELDS)
        }

    @property
    def shared_db_path(self) -> str:
        return os.path.join(self.data_dir, "shared", "self_model.db")

    @property
    def resolved_tools_workspace(self) -> str:
        """Absolute path to the filesystem workspace for sandboxed tools."""
        root = self.tools_workspace.strip() or os.path.join(self.data_dir, "workspace")
        return os.path.realpath(root)

    def user_data_dir(self, rel_key: str) -> str:
        """Per-user data directory.  rel_key is platform:user_id."""
        safe = rel_key.replace(":", "_")
        return os.path.join(self.data_dir, safe)

    def user_db_path(self, rel_key: str) -> str:
        return os.path.join(self.user_data_dir(rel_key), "nur.db")

    def user_state_path(self, rel_key: str) -> str:
        """Legacy per-user engine-state path kept for backward-compatible restore."""
        return os.path.join(self.user_data_dir(rel_key), "engine_state.json")

    def session_state_path(self, session_key: str) -> str:
        """Per-session engine-state path.

        Session state is keyed by ``platform:user_id:chat_id`` so different chat
        contexts for the same user restore independently while still sharing the
        same per-user relational storage.
        """
        platform, user_id, chat_id = session_key.split(":", 2)
        rel_key = f"{platform}:{user_id}"
        safe_chat = _safe_path_token(chat_id)
        return os.path.join(self.user_data_dir(rel_key), "sessions", f"{safe_chat}.json")

    def session_history_path(self, session_key: str) -> str:
        """Per-session hot transcript path restored until the session is ended."""
        state_path = self.session_state_path(session_key)
        root, _ext = os.path.splitext(state_path)
        return f"{root}.history.json"


def _safe_path_token(value: str) -> str:
    """Filesystem-safe token for path segments derived from runtime keys."""
    return (
        value.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace("..", "_")
    )
