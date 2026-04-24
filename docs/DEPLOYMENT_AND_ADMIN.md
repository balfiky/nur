# Deployment And Admin Guide

This guide is for running Nūr as an open-source product instance, not just as a
local research demo.

Use it together with:

- [SECURITY.md](../SECURITY.md) for threat model and sensitive surfaces
- [PRIVACY.md](../PRIVACY.md) for retention, inspection, and deletion
- [ADMIN_CONSOLE_DESIGN.md](ADMIN_CONSOLE_DESIGN.md) for the admin-console
  product direction

## Recommended Local Install

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
cp runtime_config.example.yaml runtime_config.yaml
nur-web
```

Open:

- Chat UI: `http://localhost:8000`
- Admin console: `http://localhost:8000/admin`
- API docs: `http://localhost:8000/docs`

The starter config uses `llm_backend: mock`, so no provider key is required for
first boot.

## Admin Console

The `/admin` route serves the built-in operator console. It uses the same
runtime config primitives as `runtime_config.yaml`; it is not a separate config
system.

Use it to:

- Configure LLM provider, model, base URL, and API keys
- Configure Telegram token, allowlist, polling, and dedupe settings
- Configure bearer auth, CORS, tools, shell-tool opt-in, and workspace paths
- Test LLM, Telegram, and storage settings before relying on them
- Inspect diagnostics: Python version, uptime, active sessions, storage paths
- Export a redacted runtime config
- Create a local zip backup under `<data_dir>/backups`
- Reset one active session with typed confirmation
- Delete one user's persisted data with typed confirmation

Secret values are never returned by admin API responses. Blank secret fields
mean "keep the current value"; explicit clear checkboxes remove stored YAML
secrets.

## First Production Hardening Checklist

Before binding Nūr beyond your own machine:

1. Set `api_key` in `/admin` or `runtime_config.yaml`.
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
| `api_key` | Set this before exposing the server. It protects `/admin`, `/chat`, `/config`, `/ws`, and `/v1/*` except health/ready. |
| `llm_backend` | Use `provider` for hosted gateways, `openai_compatible` for local/remote compatible servers, `mock` for offline tests. |
| `llm_base_url` / `llm_model` | Required for hosted and OpenAI-compatible backends. |
| `llm_api_key` | Generic provider/gateway key. Prefer local-only YAML or environment injection. |
| `telegram_token` | Enables the Telegram channel when set. |
| `telegram_allowlist` | Numeric Telegram user IDs allowed to talk to the bot. |
| `cors_origins` | Browser origin allowlist. Empty means same-origin only. |
| `data_dir` | Persistent data root; default is `data`. |
| `tools_enabled` | Master switch for tool execution; off by default. |
| `tools_workspace` | Filesystem sandbox root for tools; blank means `<data_dir>/workspace`. |
| `shell_tool_enabled` | Separate opt-in for subprocess execution; keep off by default. |

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

For source installs:

```bash
git pull
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/test_interface.py tests/test_interface_v1.py -q
```

Then restart `nur-web`.

If your deployment has real user data, create a backup before pulling changes.

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
