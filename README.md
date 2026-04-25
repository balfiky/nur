# Project Nūr

![Persistent cognitive state across turns](docs/diagrams/hero-banner.png)

**Nūr is an experimental AI assistant runtime with persistent emotional memory.**

Most LLM assistants can sound warm, hurt, careful, or attached for one reply, but the feeling resets on the next turn. Nūr experiments with a different idea: emotional state, memory, and relationship context should persist, decay, and shape future replies.

Under the hood, Nūr is a Python cognitive runtime that treats emotion, memory, and relationship context as *persistent state* — not a prompt-level style layer. It does not give an AI real feelings; it gives an LLM a structured state layer for mood, trust, tension, repair, commitments, and relationship continuity.

[![CI](https://github.com/balfiky/nur/actions/workflows/ci.yml/badge.svg)](https://github.com/balfiky/nur/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![version](https://img.shields.io/badge/version-0.26.1-informational)](CHANGELOG.md)

> Open-source research/product prototype. It does not claim consciousness, real feelings, therapy, diagnosis, or psychological validity.
> Nūr persists relational and semantic memory under `data/`. Use it with consent when other people are involved, and see [PRIVACY.md](PRIVACY.md) for inspection, export, and deletion.

## What That Feels Like

```text
Mon  You:  I shipped the feature!
     Nūr:  That's great — how did it land?
                                 # writes memory · valence += positive

Wed  You:  The rollback was rough.
     Nūr:  I remember Monday went well. What flipped?
                                 # retrieves Mon · opens unresolved loop · bonding holds

Fri  You:  Fixed it. Feeling better.
     Nūr:  Good. Want to unpack what actually broke?
                                 # closes loop · records repair · trust accumulates
```

The LLM writes language. Deterministic state, memory retrieval, and safety gates live *outside* the model — so the assistant's stance toward you accumulates instead of resetting.

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

## Quickstart

```bash
pip install project-nur
nur-web
```

Open http://localhost:8000. A first-run wizard walks you through LLM backend and agent identity.

To keep your customized agent identity across `pip install --upgrade`:

```bash
export NUR_CONFIG_DIR=~/.config/nur
mkdir -p "$NUR_CONFIG_DIR"
```

### From Source

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m pip install -e ".[dev]"
nur-web                    # web UI at :8000
nur                        # console runtime
```

## Configure An LLM

The setup wizard or `/admin` console is the preferred path.

| Backend | Use case |
|---|---|
| `mock` | Offline/local testing with deterministic mock responses |
| `provider` | Hosted OpenAI-compatible gateways |
| `openai_compatible` | Local/remote servers (Ollama, LM Studio, vLLM) |
| `minimax` | Legacy MiniMax-specific path |
| `auto` | Compatibility fallback; warns when no LLM is configured |

Runtime config is `runtime_config.yaml` in the current working directory. Identity is `config/soul.yaml`, or `$NUR_CONFIG_DIR/soul.yaml` when the override is set.

## Architecture At A Glance

![Project Nūr runtime architecture](docs/diagrams/runtime-architecture.png)

Two entry points share the same session and cognition layer:

- `nur-web` — FastAPI, bundled chat UI, `/admin`, legacy endpoints, `/v1/*`
- `nur` — console, Telegram, debug runtime

The core turn path runs `SessionManager` → `UserSession` → `CognitivePipeline`. The LLM writes language; deterministic state, memory, safety gates, and retrieval happen around it. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

OpenAPI docs at http://127.0.0.1:8000/docs when the server is running.

## Production Posture

Before exposing Nūr beyond localhost:

- Set `api_key` so admin and chat endpoints require bearer auth
- Keep `tools_enabled: false` and `shell_tool_enabled: false` unless explicitly needed
- Set Telegram allowlists before enabling a bot
- Treat `data/`, `runtime_config.yaml`, and backups as sensitive
- Read [docs/DEPLOYMENT_AND_ADMIN.md](docs/DEPLOYMENT_AND_ADMIN.md), [SECURITY.md](SECURITY.md), and [PRIVACY.md](PRIVACY.md)

## Common Commands

```bash
python3 -m pytest                                # full suite (1432 tests, ~50s)
python3 -m pytest tests/test_interface.py -q     # focused interface tests
python3 -m evals --backend mock --tag phase11    # offline behavioral eval pack
python3 -m build --sdist --wheel                 # build wheel and sdist
```

## Repository Layout

```text
config/     YAML config and prompt templates
core/       Cognitive state, appraisal, memory, profiles, dual process
runtime/    Runtime config, channels, session lifecycle, tool factory
interface/  FastAPI app, /v1 API, client, bundled web UI
evals/      Behavioral scenario and ablation harness
tests/      Unit, integration, regression, runtime, and API tests
docs/       Public docs, design docs, diagrams, research notes
```

## Current Status

Version `0.26.1`. 1432 tests. Reproducible eval evidence is intentionally narrow: relationship memory is load-bearing under the current Phase 11 scenarios. The project does not yet claim human-likeness, therapeutic value, or psychological validity.
