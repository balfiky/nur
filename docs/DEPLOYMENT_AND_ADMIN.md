# Deployment And Admin Guide

This guide is for running Nūr as an open-source product instance, not just as a
local research demo.

Use it together with:

- [SECURITY.md](../SECURITY.md) for threat model and sensitive surfaces
- [PRIVACY.md](../PRIVACY.md) for retention, inspection, and deletion

## Recommended Local Install

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
nur-web
```

For a normal local setup, run:

```bash
nur-setup
```

That command creates `runtime_config.yaml`, creates data/workspace folders,
prompts for model backend, identity, Telegram, and tool settings, and exits. It
does not start a browser.

Start the browser UI when you want it:

```bash
nur-web --config runtime_config.yaml
```

If you prefer the browser wizard instead of terminal prompts, run it explicitly:

```bash
nur-setup --web --config runtime_config.yaml
```

Open:

- Chat UI: `http://localhost:8000`
- Admin console: `http://localhost:8000/admin`
- API docs: `http://localhost:8000/docs`

A first-run setup wizard opens automatically on first launch. It walks through
LLM backend selection, seed identity, and optional first Life History material
in five steps (Welcome → Connect LLM → Identity → First Experience → Done).
Life History material can be pasted or uploaded from the browser as text or
Markdown, so a normal user does not need to place files into the data directory.
You can skip it and come back via
**Settings → Setup → Launch Setup Wizard**.

## Git Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install "git+https://github.com/balfiky/nur.git"
export NUR_CONFIG_DIR=~/.config/nur   # see below — do this before the first run
mkdir -p "$NUR_CONFIG_DIR"
nur-setup
```

Nūr is not published on PyPI yet. `pip install project-nur` will only work
after a public wheel is released.

For local-only use, the default `nur-web` command binds to `127.0.0.1`. For
LAN/public testing:

```bash
nur-web --host 0.0.0.0 --port 8000 --config runtime_config.yaml
```

No API token is required by default. If you later set `api_key`, the browser
admin console has an **API Token** button for that hardened mode.

`nur-web` now also accepts `--config /path/to/runtime_config.yaml` when you need
to run from a systemd working directory that differs from the config location.

## Uninstalling A Local Workspace

For a normal local install, preview removal first:

```bash
nur-uninstall --dry-run
```

Then remove the Nūr-created runtime config and data directory:

```bash
nur-uninstall
```

The command requires typing `uninstall` before it deletes anything. It removes
`runtime_config.yaml` and the configured `data_dir`. It does **not** delete a
custom `tools_workspace` outside `data_dir` unless you pass
`--remove-external-workspace`, and it does **not** delete
`$NUR_CONFIG_DIR/soul.yaml` unless you pass `--remove-identity`.

After the workspace is removed, uninstall the Python package if desired:

```bash
python3 -m pip uninstall project-nur
```

## NUR_CONFIG_DIR — keeping your identity across upgrades

When installed from a packaged distribution, `config/soul.yaml` lives inside the
package directory and may be overwritten on package upgrade. Set
`NUR_CONFIG_DIR` to a directory you control before the first run:

```bash
export NUR_CONFIG_DIR=~/.config/nur
mkdir -p "$NUR_CONFIG_DIR"
```

With the env var set:
- The admin console writes `soul.yaml` to `$NUR_CONFIG_DIR/soul.yaml` instead
  of the package-internal path.
- On `pip install --upgrade`, your customized identity is untouched.
- The admin console shows a note when the env var is **not** set so operators
  don't silently lose their identity on the next upgrade.

The env var only governs `soul.yaml`. `runtime_config.yaml` is normally written
to the current working directory, or to the explicit path passed through
`nur-setup --config` / `nur-web --config`.

For systemd or Docker deployments, pass it as an environment variable:

```bash
# systemd
Environment=NUR_CONFIG_DIR=/etc/nur

# Docker
docker run -e NUR_CONFIG_DIR=/config -v /host/config:/config ...
```

## Admin Console

The `/admin` route serves the built-in operator console. It uses the same
runtime config primitives as `runtime_config.yaml`; it is not a separate config
system.

The chat UI, setup wizard, debug panel, and embedded settings drawer use the
same light operator palette as the standalone console so configuration and
observability remain readable during normal use.

The chat header links directly to `/admin` for full-screen operator work. The
embedded settings drawer remains available for quick edits and setup-wizard
access, but the full console is the preferred space for skills, life-history
observability, maintenance, and careful production configuration.

The **Setup Wizard** (accessible from Settings → Setup → Launch Setup Wizard)
walks through LLM backend, agent identity, optional first formative material,
and completion in five steps. It is the recommended path for first-time
configuration.

The full console lets you:

- Configure LLM provider, model, base URL, and API keys
- Configure Telegram token, allowlist, polling, and dedupe settings
- Configure bearer auth, CORS, tools, shell-tool opt-in, and workspace paths
- Upload, import, audit, and enable external Agent Skills
- Feed pasted or uploaded formative text/Markdown files into **Life History**
- Observe experience count, evolution events, current beliefs, and drive shifts
- Test LLM, Telegram, and storage settings before relying on them
- Inspect diagnostics: Python version, uptime, active sessions, storage paths
- Export a redacted runtime config
- Create a local zip backup under `<data_dir>/backups`
- Reset one active session with typed confirmation
- Delete one user's persisted data with typed confirmation

Secret values are never returned by admin API responses. Blank secret fields
mean "keep the current value"; explicit clear checkboxes remove stored YAML
secrets.

## Skills Admin

The **Skills** page imports uploaded `SKILL.md`/Markdown files, zipped skill
folders, server-side skill folders, or pasted `SKILL.md` content. Browser upload
is the normal path; server paths are an advanced escape hatch. Imported skills
are disabled until review. The audit reports metadata, tool hints, risk flags,
bundled scripts, resource counts, and unsupported platform-specific hints.

Enabled skills are injected into generation as bounded private guidance. They
do not execute bundled scripts or bypass tool policy. If a skill needs web,
filesystem, shell, or browser actions, those actions still require the normal
runtime tool settings, workspace restrictions, auth posture, and shell opt-in.

## First Production Hardening Checklist

Before binding Nūr beyond your own machine:

1. Leave `api_key` empty for the simplest personal setup, or set it only when
   you deliberately want bearer-token auth.
2. Keep `cors_origins: []` unless a separate browser origin needs access.
3. Keep `tools_enabled: false` until you explicitly need agentic tools.
4. Keep `shell_tool_enabled: false` unless every authenticated user is trusted.
5. Set `telegram_allowlist` before enabling a Telegram bot.
6. Confirm `data_dir` points to a durable, backed-up location.
7. Run `/admin/test/llm`, `/admin/test/telegram`, and `/admin/test/storage`.
8. Review [PRIVACY.md](../PRIVACY.md) before letting other people interact with
   the instance.

The safe default posture is localhost-only, mock backend, tools off, shell off,
and no public CORS origins.

## Runtime Config

`runtime_config.yaml` is the source of truth for deployment settings. The admin
console reads and writes this file.

Important fields:

| Field | Production guidance |
|-------|---------------------|
| `api_key` | Optional bearer token for hardened deployments. Empty means no browser/API token flow. When set, it protects `/admin`, `/chat`, `/config`, `/ws`, and `/v1/*` except health/ready. |
| `llm_backend` | Use `provider` for hosted gateways, `openai_compatible` for local/remote compatible servers, `mock` for offline tests. |
| `llm_base_url` / `llm_model` | Required for hosted and OpenAI-compatible backends. |
| `llm_api_key` | Generic provider/gateway key. Prefer local-only YAML or environment injection. |
| `telegram_token` | Enables the Telegram channel when set. |
| `telegram_allowlist` | Numeric Telegram user IDs allowed to talk to the bot. |
| `cors_origins` | Browser origin allowlist. Empty means same-origin only. |
| `data_dir` | Persistent data root; default is `data`. |
| `tools_enabled` | Master switch for tool execution; off by default. |
| `tools_workspace` | Filesystem sandbox root for tools; blank means `<data_dir>/workspace`. |
| `shell_tool_enabled` | Separate opt-in for local shell execution; keep off by default. Supports normal shell syntax such as pipes once enabled. |

## Telegram Commands

Telegram command handling is local to the runtime channel and does not trigger
the typing loop or normal chat generation.

| Command | Behavior |
|---|---|
| `/help` or `/commands` | Show the supported command list. |
| `/start` | Send a short greeting plus the command list. |
| `/status` | Show the active session's emotional modulators, if one exists. |
| `/mental` or `/mood` | Create/inspect the current session and report mental-state diagnostics. |
| `/new` | Start a fresh hot conversation while keeping relationship memory. Clears the active hot transcript. |
| `/reset` | Digest, save, and close the active session. Clears the active hot transcript. |
| `/debug` | Show a compact last-turn debug summary for the active session. |

Telegram keeps the active hot transcript under
`<data_dir>/telegram_<user_id>/sessions/<chat_id>.history.json` and restores it
after idle eviction or runtime restart. The transcript is separate from
relationship and semantic memory; `/new`, `/reset`, and explicit session-end
operations clear it.

## Life History Admin

The **Life** page is the first observability surface for Nūr's identity-level
experience ledger. It supports:

- Pasted text intake for short formative material
- Browser upload intake for text/Markdown/reStructuredText files
- Advanced local text/Markdown file intake from inside `tools_workspace`
- Explicit conversation learning intake when a user says "learn from" /
  "study this" / "digest" with a URL or sufficiently long pasted material
- An evolution snapshot for first/latest experience, dominant drive,
  strongest drive drift, and change-type mix
- An evolution timeline of belief, drive, self-trait, and worldview changes
- An experience ledger showing what was ingested and why it was salient
- Current belief and drive summaries

After intake, normal runtime sessions retrieve a compact generation context
from this ledger: current beliefs, drive shifts, and recent evolution events.
Raw excerpts stay in the admin/SQLite ledger; they are not injected into every
chat prompt.

Conversation learning is deliberately explicit. A request such as "learn from
this project https://github.com/..." fetches readable source text, records the
URL as `source_ref`, writes evolution events, and appends a short learning
receipt to the reply. A normal web search or casual discussion does not write
to Life History.

Data is stored in `data/shared/life_history.db`. This is shared assistant
identity data, not per-user chat memory. User deletion does not remove it.
For a full identity reset, stop the service and remove both
`data/shared/self_model.db` and `data/shared/life_history.db` after taking
any backup you need.

## Running The Web Server

Development/local:

```bash
nur-web
```

Fallback:

```bash
python3 -m interface.api
```

By default this starts Uvicorn on `127.0.0.1:8000`. If you run behind a reverse
proxy, terminate TLS at the proxy and keep Nūr bound to localhost unless you
have a specific reason not to.

Example reverse-proxy shape:

```text
Internet -> HTTPS reverse proxy -> http://127.0.0.1:8000
```

Nūr does not currently ship a systemd unit or Dockerfile. For production-like
usage today, run it under your existing process supervisor and persist both
`runtime_config.yaml` and `data_dir`.

## Backup And Export

From `/admin`:

- **Export Config** downloads a JSON export with secret values redacted.
- **Create Backup** creates a zip archive under `<data_dir>/backups`.

The backup includes:

- `runtime_config.redacted.json`
- data files under `data_dir`

The backup intentionally skips the backup directory itself to avoid recursive
archives.

Backups may still contain user memory data. Treat backup files as sensitive
even though runtime config secrets are redacted.

## Reset And Delete

Destructive admin actions require exact typed confirmations:

- Reset session: confirmation must be `RESET <session_key>`
- Delete user: confirmation must be `DELETE <platform>:<user_id>`

User deletion removes only the selected user's per-user data directory and live
sessions. It preserves `data/shared/self_model.db` by design. See
[PRIVACY.md](../PRIVACY.md) for the full deletion contract.

There is no broad "delete all data" control. If you need a full local reset,
stop the service and remove `data_dir` manually after taking a backup.

## Updating

**Source install:**

```bash
git pull
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/test_interface.py tests/test_interface_v1.py -q
```

**Git install:**

```bash
python3 -m pip install --upgrade "git+https://github.com/balfiky/nur.git"
```

Then restart `nur-web`.

If `NUR_CONFIG_DIR` is set, your identity file is untouched. If it is not set,
a package upgrade may overwrite the bundled `config/soul.yaml` with the
defaults shipped in the new package.

If your deployment has real user data, create a backup before upgrading.

## Operational Notes

- Health probe: `GET /v1/health`
- Readiness probe: `GET /v1/ready`
- Admin diagnostics: `GET /admin/diagnostics`
- Stable integration API: `/v1/*`
- Built-in OpenAPI docs: `/docs`

When `api_key` is set, admin and integration calls need:

```bash
Authorization: Bearer <api_key>
```

The browser UI stores the token locally in the browser after you enter or rotate
it through the admin console.
