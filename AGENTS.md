# A2A Knowledge Mesh — Agent Guide

## Setup

```bash
uv sync                       # install deps (uv, not pip)
cp .env.example .env
# fill in tokens: openssl rand -hex 32
```

## Running

```bash
bash scripts/run_mesh.sh                    # Band-native hackathon demo
uv run python mesh.py status                # local SQLite debug queries
uv run python test_integration.py           # legacy HTTP A2A e2e test
```

Env is loaded from `.env` by each agent entrypoint (dotenv in runner + each module).

## Architecture

Hackathon path: 5 Band-connected agents in one shared room:

| Agent | Port | Module | Skills |
|-------|------|--------|--------|
| Scraper | Band | `agents/scraper_band.py` | slurp-git, extract-facts |
| Keeper | Band | `agents/keeper_band.py` | store, recall, list, detect, reset-demo |
| Reconciler | Band | `agents/reconciler_band.py` | detect, status, resolve, clear |
| Registry | Band | `agents/registry_band.py` | register, discover, list, reset-demo |
| Bridge | 8775 | `agents/bridge_agent.py` | dashboard event mirror |

**Agent base classes:**
- `agents/base.py` → `Agent` ABC (A2A HTTP server, auth, health, RPC dispatch)
- `agents/band_agent.py` → `BandAgent` (WebSocket agent via `band-sdk` SimpleAdapter)

Two agent flavors coexist: Band-native (`agents/*_band.py`) and local HTTP A2A (`agents/{registry,keeper,reconciler}.py`). Band-native agents are the primary hackathon demo. HTTP A2A agents remain for local protocol tests and reusable SQLite store classes.

**Transports:** Band rooms/WebSocket for the hackathon workflow. Legacy HTTP uses JSON-RPC 2.0 over `POST /a2a`, authenticated via bearer token + optional HMAC body signature. Agent cards at `GET /.well-known/agent-card.json`.

**Auth:** Each role has its own bearer token (`A2A_{ROLE}_TOKEN`). `A2A_MASTER_TOKEN` is the cross-role fallback. `A2A_HMAC_SECRET` (32+ byte hex) signs request bodies for agent-to-agent calls. Public endpoints (health, card) are unauthenticated.

**Shared modules:**
- `agents/auth.py` — token validation, HMAC signing, pooled httpx client for A2A calls
- `agents/provider.py` — LLM provider (Featherless → OpenAI), single `provider.chat_completion()` call
- `agents/validation.py` — Pydantic models for all RPC params
- `protocols/a2a.py` — A2A protocol dataclasses (AgentCard, A2AResponse, error codes)
- `protocols/json_parser.py` — resilient LLM JSON parser (fences, trailing commas, dirtyjson)

## Data

SQLite in `data/{registry,keeper,reconciler}.db` (gitignored). Each agent owns its store. Facts are RDF-lite: (subject, predicate, object, source_id, timestamp). Conflicts detected via SQL JOIN — no O(n²) memory scan.

## LLM Provider

Env-driven chain: `FEATHERLESS_API_KEY` → Featherless, fallback `OPENAI_API_KEY` → OpenAI. Uses `httpx` client, not the `openai` SDK package. Retries (2 attempts, 1.5s delay). Used by Reconciler conflict suggestions and scoring.

## Key Conventions

- Python 3.11+, ruff linting (`line-length = 100`)
- No typechecker configured
- pyproject.toml scripts entry: `mesh-runner = "agents.runner:main"`
- No pytest required — `test_unit.py` uses stdlib `unittest`; `test_integration.py` is the legacy HTTP e2e with auto-generated auth tokens
- `scripts/git_scraper.py` and `scripts/scraper.py` are standalone scrapers (not part of the agent mesh)
- `spikes/` contains feasibility validation experiments
- No FastAPI — pure Starlette + uvicorn
