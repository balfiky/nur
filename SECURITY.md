# Security Policy

## Supported Versions

Project Nūr is currently a research prototype. Only the `main` branch is
supported. There are no backported security fixes.

## Reporting a Vulnerability

Please **do not** file public GitHub issues for security or privacy
vulnerabilities. Instead, email the maintainer directly:

- **Contact:** balfiky@yahoo.com
- **Subject prefix:** `[nur-security]`

Please include:

- A description of the issue and the impact
- Steps to reproduce, ideally with a minimal script or config
- Your suggested fix if you have one

You should receive an acknowledgement within a few business days. We'll work
with you on a disclosure timeline. Public disclosure should wait until a fix
is available or we've agreed on a timeline.

## Known Sensitive Surfaces

These areas process or persist potentially sensitive user content and warrant
extra scrutiny:

- `runtime/sessions/persistence.py` — writes per-session engine state to
  `data/<platform>_<user_id>/sessions/<chat_id>.json`. `data/` is gitignored;
  never commit it.
- `core/memory/long_term.py`, `core/memory/relationship.py`,
  `core/memory/semantic.py` — SQLite tables in `data/<platform>_<user_id>/nur.db`
  storing distilled user memory (`memories`), relationship events
  (`relationship_events`), open loops (`open_loops`), and semantic preferences
  (`semantic_memories`). The shared self-model lives in
  `data/shared/self_model.db`. See [PRIVACY.md](PRIVACY.md) for full layout.
- `runtime_config.yaml` — may contain API keys. The secret fields
  (enumerated in `runtime/config.py:_SECRET_FIELDS`) are `telegram_token`,
  `llm_api_key`, `minimax_api_key`, and `api_key`. Keep real values out of
  committed files; use `runtime_config.example.yaml` as the tracked starter
  and inject real values locally or via environment variables.
- `interface/api.py` and `interface/v1.py` — CORS and bearer-auth handling.
  The default `cors_origins: []` is same-origin only; avoid widening it
  without careful thought.
- `runtime/channels/telegram.py` — allowlist logic controls who can chat.
  Misconfiguration exposes the system to arbitrary Telegram users.

## Responsible Use

Nūr persists emotional and relational state about its users. Do not deploy it
against people who have not consented to that persistence. See
[PRIVACY.md](PRIVACY.md) for retention and deletion guidance.
