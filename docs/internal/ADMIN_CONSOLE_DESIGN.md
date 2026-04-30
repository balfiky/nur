# Nūr Admin Console Design

This document defines the production direction for Nūr's first-run setup and
permanent admin settings surface.

The goal is not to add a second runtime or a separate configuration system.
The goal is to turn the existing web Settings drawer, `/config` route, and
`RuntimeConfig` model into a complete operator console for open-source
installations.

## Product Goal

An operator should be able to install Nūr, open the web UI, configure the
runtime, test the setup, and keep managing the instance without editing YAML
unless they choose to.

The console should support two modes:

1. **First-run setup wizard** for a new installation.
2. **Permanent admin console** for ongoing changes, diagnostics, backup, and
   maintenance.

Both modes must use the same backend configuration primitives so the product
does not fork into "wizard config" and "runtime config".

## Current Surfaces

Nūr already has the important foundation:

- `runtime/config.py` owns `RuntimeConfig`, YAML persistence, redacted public
  serialization, and secret-status reporting.
- `interface/api.py` exposes `GET /config` and `POST /config` for the bundled
  web UI.
- `interface/static/index.html` includes a settings drawer with LLM, access,
  Telegram, runtime, and proactive fields.
- `interface/v1.py` exposes stable integration endpoints and redacted
  `GET /v1/config`.
- `runtime_config.example.yaml` documents safe defaults.

Milestone 1 should evolve these surfaces instead of replacing them.

## Design Principles

### One Config Source

`RuntimeConfig` remains the source of truth for runtime settings.

The admin console may add metadata such as validation status or setup progress,
but effective runtime values must still resolve from `runtime_config.yaml`,
environment variables, and the existing runtime factories.

### Safe Secrets

Secrets must never be returned by API responses.

The UI should only show:

- whether a secret is configured
- the source class when useful: YAML, environment, or unset
- controls to replace or clear a stored YAML secret

The API should keep the existing "blank means keep current" behavior and should
continue to support explicit clear flags.

### Auth Before Exposure

The setup and admin pages may stay open for localhost-only starter use, but the
product should strongly push users to configure `api_key` before enabling remote
access, Telegram, or tools.

If Nūr is bound beyond localhost, the admin console should treat missing
`api_key` as a high-severity warning.

### Test Before Save When Possible

Settings that connect to external services should have test actions:

- LLM provider connection
- Telegram bot token and allowlist
- data directory writability
- tools workspace sandbox
- CORS/auth configuration

Save should remain possible for offline setup, but the UI should clearly show
untested or failing sections.

### No Hidden Runtime Restarts

Every setting should declare its application behavior:

- **Live reload**: applied immediately by the standalone web runtime.
- **Session manager reload**: active sessions are shut down and rebuilt.
- **Restart required**: `nur` / `python3 main.py` or process-level settings need
  a restart.

The existing `/config` response already reports manager reload; milestone 1
should formalize this per field.

## Target UX

### First-Run Wizard

The wizard appears when `runtime_config.yaml` is missing, minimal, or clearly
unconfigured.

Recommended steps:

1. **Welcome and mode**
   - local-only development
   - private server
   - public/reverse-proxied server
2. **Access**
   - configure `api_key`
   - show remote-exposure warning when auth is empty
3. **LLM backend**
   - choose `mock`, `provider`, `openai_compatible`, or `minimax`
   - enter base URL, model, and key when needed
   - test provider connection
4. **Channels**
   - Telegram token and allowlist
   - test bot token
5. **Storage**
   - data directory
   - validate writable path
6. **Tools**
   - default off
   - explain filesystem sandbox
   - require auth warning before enabling
7. **Review**
   - show changes
   - save config
   - show restart/reload requirements

The wizard should end in the normal admin console, not a separate page.

### Terminal Setup Follow-Ups

The `nur-setup` terminal flow should collect the network binding target before
it prints run instructions:

- bind host/IP, defaulting to `127.0.0.1`
- port, defaulting to `8000`
- a warning when binding to `0.0.0.0` or another non-loopback address without
  a configured access token

The generated completion message should then show the exact `nur-web --host ...
--port ...` command for the selected values. This keeps setup terminal-native
while avoiding a hidden assumption about localhost and port 8000.

### Permanent Admin Console

The permanent console should replace the current single drawer layout with
sections that can grow:

- **Overview**
  - health, readiness, backend, auth status, Telegram status, tool status
- **Model**
  - backend, base URL, model, key status, test action
- **Access**
  - bearer token, CORS, bind/readiness warnings
- **Channels**
  - Telegram config, allowlist, poll settings, test action
- **Runtime**
  - data directory, queue/session limits, timeout, debug host/port
- **Tools**
  - master enable switch, workspace, shell opt-in, sandbox validation
- **Memory and Data**
  - data path summary, user reset links, future backup/export controls
- **Diagnostics**
  - recent config errors, provider failures, Telegram failures, startup warnings

The current chat UI can still have a Settings button, but production use should
have a clear `/admin` or equivalent route.

## API Direction

Keep the existing legacy `/config` for the bundled UI and compatibility, but
add admin-specific endpoints rather than overloading one route with every new
operation.

Recommended additions:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/config` | Full redacted config plus field metadata |
| POST | `/admin/config` | Save config changes with explicit secret actions |
| GET | `/admin/status` | Health, readiness, config warnings, runtime reload state |
| POST | `/admin/test/llm` | Validate selected or saved LLM settings |
| POST | `/admin/test/telegram` | Validate Telegram token and basic bot access |
| POST | `/admin/test/storage` | Validate data directory and tool workspace |
| POST | `/admin/setup/complete` | Mark first-run setup complete |

These routes should use the same bearer behavior as `/config`: if `api_key` is
set, require it. If no `api_key` is set, allow localhost bootstrap but emit a
warning in `/admin/status`.

Do not put these endpoints under `/v1` yet. `/v1` is the stable integration API
for clients. Admin operations are product-operator surface area and may change
while the console matures.

## Config Metadata

`RuntimeConfig.to_public_dict()` is enough for current settings, but the admin
console needs richer field metadata.

Add a backend helper that returns a schema-like structure:

- field name
- current redacted value
- default value
- section
- secret status
- allowed values
- whether restart is required
- whether the field supports live reload
- validation warnings

This can be implemented as a static registry near `runtime/config.py` or in a
new `runtime/admin_config.py` module. Keep it Python-native; do not introduce a
new config DSL.

## First-Run Detection

Use conservative detection. The wizard should appear when one of these is true:

- `runtime_config.yaml` does not exist
- `runtime_config.yaml` exists but was generated from defaults and setup is not
  marked complete
- an explicit admin state file says setup is incomplete

Store setup completion outside `RuntimeConfig`, for example:

```text
data/admin_state.json
```

Suggested shape:

```json
{
  "setup_completed": true,
  "completed_at": 1710000000.0,
  "last_config_save_at": 1710000000.0
}
```

This avoids turning operational UI state into cognitive runtime config.

## Validation Rules

Validation should be clear and bounded:

- `llm_backend=mock`: no provider fields required
- `llm_backend=provider`: require `llm_base_url`, `llm_model`, and either a
  stored or environment-provided key unless the operator explicitly marks the
  provider as keyless
- `llm_backend=openai_compatible`: require `llm_base_url` and `llm_model`; key
  optional for local deployments
- `llm_backend=minimax`: require `minimax_api_key` or `MINIMAX_API_KEY`
- Telegram enabled means `telegram_token` is configured
- `tools_enabled=true` should warn if `api_key` is empty
- `shell_tool_enabled=true` should warn unless `tools_enabled=true` and
  `tools_workspace` resolves inside an expected writable directory
- public CORS origins with empty `api_key` should be a high-severity warning

Validation warnings should not be hidden in logs. They should appear in the
admin overview and in save/test responses.

## Backup, Export, And Reset

This belongs in the permanent console but should not block the first admin
milestone.

The design reserves space for:

- export `runtime_config.yaml` with secrets removed through
  `GET /admin/export/config`
- backup `data/` safely through `POST /admin/backup`
- inspect runtime/storage health through `GET /admin/diagnostics`
- reset one active session through `POST /admin/sessions/reset` with typed
  confirmation
- delete one user through `POST /admin/users/delete` with typed confirmation,
  preserving the shared self-model database
- future encrypted export/import

Do not add broad "delete all data" unless it has explicit confirmation and
tests.

## Security Requirements

Milestone 1 implementation must preserve these rules:

- no API response returns raw secret values
- settings mutation is protected when `api_key` is configured
- WebSocket auth behavior remains unchanged
- enabling tools never silently enables shell execution
- config save never resets hidden tool fields accidentally
- admin test endpoints do not leak provider keys in errors
- admin export/backup artifacts do not include raw config secrets
- admin reset/delete actions require exact typed confirmations
- startup/local-bootstrap behavior remains usable with `api_key=''`

## Test Plan

Add focused tests before broad UI work:

- admin config response redacts secrets and reports secret status
- admin status reports auth disabled, backend, tools, and warnings
- LLM validation rejects missing base URL/model for hosted backends
- OpenAI-compatible validation allows local keyless endpoints
- Telegram validation reports missing token without starting polling
- saving admin config preserves existing secrets when blank
- clear flags remove stored secrets
- tool settings are preserved unless explicitly changed
- admin routes require bearer token when `api_key` is set
- admin diagnostics reports runtime and storage status
- redacted config export contains no raw secret values
- backup creates a local zip without recursively backing up prior backups
- session reset requires typed confirmation and evicts one active session
- user delete requires typed confirmation, rejects path traversal, and wipes
  only the selected user's data
- first-run detection handles missing config, default config, and completed
  setup state

## Implementation Phases

### Phase 1: Backend Admin Contract

- Add admin status/config payload helpers.
- Add validation helpers.
- Add `/admin/status` and `/admin/config`.
- Add tests for redaction, auth, warnings, and save semantics.

### Phase 2: Test Actions

- Add LLM, Telegram, and storage test endpoints.
- Keep test endpoints side-effect-light.
- Add tests for success, missing config, and redacted errors.

### Phase 3: UI Conversion

- Convert the drawer into an admin console route or admin panel.
- Add setup wizard state.
- Surface warnings and test results.
- Add tool settings to the UI.

### Phase 4: Operator Maintenance

- Add diagnostics, redacted config export, local backup, guarded session reset,
  and guarded user delete affordances.
- Document deployment and admin use.

## Milestone 1 Acceptance Criteria

Milestone 1 is complete when:

- the architecture is documented
- current config surfaces are mapped
- backend admin contract is specified
- security rules are explicit
- implementation phases and tests are defined
- the next work can move from architecture to implementation without changing
  the product direction
