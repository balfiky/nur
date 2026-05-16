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
  `data/<platform>_<user_id>/sessions/<chat_id>.json` and active hot
  transcripts to `data/<platform>_<user_id>/sessions/<chat_id>.history.json`.
  `data/` is gitignored; never commit it.
- `core/memory/long_term.py`, `core/memory/relationship.py`,
  `core/memory/semantic.py` — SQLite tables in `data/<platform>_<user_id>/nur.db`
  storing distilled user memory (`memories`), relationship events
  (`relationship_events`), open loops (`open_loops`), and semantic preferences
  (`semantic_memories`). The shared self-model lives in
  `data/shared/self_model.db`. See [PRIVACY.md](PRIVACY.md) for full layout.
- `runtime/life_history.py` — shared identity-level experience and evolution
  storage in `data/shared/life_history.db`. Pasted text and local-file
  excerpts can be persisted as evidence for belief/drive changes.
- `runtime_config.yaml` — may contain API keys. The secret fields
  (enumerated in `runtime/config.py:_SECRET_FIELDS`) are `telegram_token`,
  `llm_api_key`, and `api_key`. Keep real values out of
  committed files; use `runtime_config.example.yaml` as the tracked starter
  and inject real values locally or via environment variables.
- `interface/api.py` and `interface/v1.py` — CORS and bearer-auth handling.
  The default `cors_origins: []` is same-origin only; avoid widening it
  without careful thought.
- `llm_backend: codex` — delegates response generation to the local Codex CLI.
  Nūr invokes it with `codex exec --sandbox read-only --ephemeral`; keep
  `NUR_CODEX_WORKDIR` unset unless the backend should have read-only context
  from a specific local directory.
- `runtime/channels/telegram.py` — allowlist logic controls who can chat.
  Misconfiguration exposes the system to arbitrary Telegram users.
- `/admin` — operator console for configuration, diagnostics, redacted export,
  backup, guarded session reset, guarded user deletion, skill management,
  and Life History intake/observability. Set `api_key` before exposing the
  server beyond localhost.

## Agentic Tool Runtime

Nūr ships with a builtin agentic tool layer (`tools/builtin/`) that can
match user text to actions like `fs.read_file`, `fs.write_file`,
`fs.delete_path`, `shell.run_command`, and web/browser/calendar calls via
regex heuristics in `core/dual_process/tool_loop.py`. **External tools are
off by default** (`tools_enabled: false`). With `tools_enabled: false`,
`runtime/tools.py` still wires Nūr's internal **skill-registry tools**
(`skills.create`, `skills.audit`, `skills.enable`, etc.) into the executor
so the assistant can manage durable skills through the observable tool
trace; filesystem, browser, web, calendar, and shell capabilities remain
excluded until `tools_enabled: true`, and `shell.run_command` additionally
requires `shell_tool_enabled: true`.

Additionally, the native tool-controller system prompt
(`nur_tools/native_orchestrator.py`) only emits the "diagnose, install,
retry" directive — which lets the model unilaterally run package-manager
commands on missing-dependency errors — when
`autonomy_level: high_risk`. Under `assisted` (default) and `autonomous`
the prompt explicitly instructs the model to report dependency failures
honestly rather than silently install packages.

When you turn external tools on:

- Filesystem tools are sandboxed to
  `tools_workspace` — paths that resolve outside that directory are
  refused. The default workspace is `<data_dir>/workspace`.
- Life History local-file intake also reads only from `tools_workspace`;
  operators should treat that workspace as readable by the assistant runtime.
- `shell.run_command` is a **separate** opt-in via `shell_tool_enabled: true`
  because subprocess execution has a larger blast radius than bounded
  file I/O.
- The HTTP surface (`/chat`, `/v1/chat`) routes user messages through the
  same regex intent parser, so authenticating the endpoint is not a
  substitute for sandboxing — an authenticated attacker (or the user's
  own mis-addressed command) can still reach the tool layer. Set
  `api_key` and keep `shell_tool_enabled: false` unless you trust every
  caller.

The `nur-web` launcher enforces this at startup: it refuses to bind to
a non-loopback interface (anything other than `127.0.0.1` / `localhost`
/ `::1`) when `api_key` is empty in the runtime config, exiting with
code 2 and a clear error. Operators with an external auth layer
(reverse proxy, VPN, Tailscale) can override the guard with
`--allow-unauthenticated-bind`.

When `api_key` is set, every mutating and data-bearing endpoint on the
standalone web server requires `Authorization: Bearer <api_key>`:
`/chat`, `/debug`, `/config` (GET and POST), `/session/end`, `/rest`,
`/ws`, every `/admin/*` JSON endpoint (status, config, test, diagnostics,
export, backup, sessions/reset, users/delete), and every `/v1/*`
endpoint except `/v1/health` and `/v1/ready`. Static assets (`GET /`,
`GET /admin` HTML shell) remain open so the bundled UI can bootstrap and
prompt for the bearer token client-side.

## Responsible Use

Nūr persists emotional and relational state about its users. Do not deploy it
against people who have not consented to that persistence. See
[PRIVACY.md](PRIVACY.md) for retention and deletion guidance and
[docs/DEPLOYMENT_AND_ADMIN.md](docs/DEPLOYMENT_AND_ADMIN.md) for deployment
hardening.
