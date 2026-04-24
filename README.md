# Project Nūr

Project Nūr is a Python cognitive-AI assistant runtime. It treats emotion as persistent internal state instead of a prompt-level style layer: mood, uncertainty, relationship memory, unresolved loops, and self-observation persist, decay, and influence later turns.

Nūr is an open-source research/product prototype, not a therapist, diagnosis tool, or claim of machine consciousness.

## Start Here

| Need | Document |
|---|---|
| Product/concept overview | [docs/OVERVIEW.md](docs/OVERVIEW.md) |
| Runtime architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Install, admin, deployment | [docs/DEPLOYMENT_AND_ADMIN.md](docs/DEPLOYMENT_AND_ADMIN.md) |
| Security model | [SECURITY.md](SECURITY.md) |
| Privacy and data deletion | [PRIVACY.md](PRIVACY.md) |
| Release history | [CHANGELOG.md](CHANGELOG.md) |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |

## 5-Minute Start

### Requirements

- Python 3.10+
- A browser for the web UI

### Install From Source

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m pip install -e ".[dev]"
```

### Install From Wheel

```bash
pip install project-nur
```

For wheel installs, set `NUR_CONFIG_DIR` before customizing the agent identity so `soul.yaml` survives package upgrades:

```bash
export NUR_CONFIG_DIR=~/.config/nur
mkdir -p "$NUR_CONFIG_DIR"
```

## Run

### Web UI

```bash
nur-web
```

Open http://localhost:8000. First launch shows a setup wizard for LLM backend and agent identity. You can reopen it from **Settings → Setup → Launch Setup Wizard**.

Fallback from a source checkout:

```bash
python3 -m interface.api
```

### Console Runtime

```bash
nur
```

Fallback from a source checkout:

```bash
python3 main.py
```

## Configure An LLM

The setup wizard and `/admin` console are the preferred path. Supported runtime modes:

| Backend | Use case |
|---|---|
| `mock` | Offline/local testing with deterministic mock responses |
| `provider` | Hosted OpenAI-compatible gateways with base URL, model, and API key |
| `openai_compatible` | Local or remote OpenAI-compatible servers such as Ollama, LM Studio, vLLM |
| `minimax` | Legacy MiniMax-specific path |
| `auto` | Compatibility fallback; warns when no LLM is configured |

Runtime config is stored in `runtime_config.yaml` in the current working directory. Identity config is stored in `config/soul.yaml` or `$NUR_CONFIG_DIR/soul.yaml` when the override is set.

## Production Posture

Before exposing Nūr beyond localhost:

- Set `api_key` so admin and chat endpoints require bearer auth.
- Keep `tools_enabled: false` and `shell_tool_enabled: false` unless explicitly needed.
- Set Telegram allowlists before enabling a bot.
- Treat `data/`, `runtime_config.yaml`, and backups as sensitive.
- Read [docs/DEPLOYMENT_AND_ADMIN.md](docs/DEPLOYMENT_AND_ADMIN.md), [SECURITY.md](SECURITY.md), and [PRIVACY.md](PRIVACY.md).

## Architecture At A Glance

![Project Nūr runtime architecture](docs/diagrams/runtime-architecture.png)

Two entry points share the same session and cognition layer:

- `nur-web` serves FastAPI, the bundled chat UI, `/admin`, legacy endpoints, and `/v1/*`.
- `nur` runs console/Telegram/debug-runtime flows.

The core turn path runs through `SessionManager`, `UserSession`, and `CognitivePipeline`. The LLM writes language; deterministic state, memory, safety gates, and retrieval happen outside the model. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## API Quick Tour

```bash
# Health, no auth required
curl http://127.0.0.1:8000/v1/health

# Chat, auth optional unless api_key is configured
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"hello","user_id":"demo","chat_id":"main"}'

# With auth enabled
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"message":"hello","user_id":"demo","chat_id":"main"}'
```

OpenAPI docs are available at http://127.0.0.1:8000/docs when the server is running.

## Common Commands

```bash
# All tests
python3 -m pytest

# Focused interface tests
python3 -m pytest tests/test_interface.py tests/test_interface_v1.py -q

# Offline behavioral eval pack
python3 -m evals --backend mock --tag phase11

# Build wheel and sdist
python3 -m build --sdist --wheel
```

## Repository Layout

```text
config/                 YAML config and prompt templates
core/                   Cognitive state, appraisal, memory, profiles, dual process
runtime/                Runtime config, channels, session lifecycle, tool factory
interface/              FastAPI app, /v1 API, client, bundled web UI
evals/                  Behavioral scenario and ablation harness
tests/                  Unit, integration, regression, runtime, and API tests
docs/                   Public docs, design docs, diagrams, research notes
```

## Current Status

Current package version: `0.26.1`.

The suite currently contains 1383 tests. The reproducible eval evidence is intentionally narrow: relationship memory is load-bearing under the current Phase 11 scenarios. The project does not yet claim human-likeness, therapeutic value, or psychological validity.
