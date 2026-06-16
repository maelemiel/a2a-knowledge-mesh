# A2A Knowledge Mesh — Agent Guide

## Setup

```bash
uv sync                       # install deps (uv, not pip)
cp .env.example .env
# fill in tokens: openssl rand -hex 32
```

## Running

```bash
uv run python -m agents.runner              # all 3 agents
uv run python -m agents.runner -a keeper    # single agent
uv run python mesh.py status                # CLI queries
uv run python test_integration.py           # 8-step e2e test
```

Env is loaded from `.env` by each agent entrypoint (dotenv in runner + each module).

## Architecture

3 A2A agents, each an independent Starlette ASGI server:

| Agent | Port | Module | Skills |
|-------|------|--------|--------|
| Registry | 8765 | `agents/registry.py` | discover, register, list |
| Keeper | 8766 | `agents/keeper.py` | store-fact, recall, list-facts, detect-conflicts |
| Reconciler | 8767 | `agents/reconciler.py` | detect-conflict, resolve, status |

**Agent base classes:**
- `agents/base.py` → `Agent` ABC (A2A HTTP server, auth, health, RPC dispatch)
- `agents/band_agent.py` → `BandAgent` (WebSocket agent via `band-sdk` SimpleAdapter)

Two agent flavors coexist: local HTTP A2A (`agents/{registry,keeper,reconciler}.py`) and Band-native (`agents/*_band.py`). The HTTP agents are the primary ones; Band agents use Band rooms for communication.

**Transport:** JSON-RPC 2.0 over HTTP `POST /a2a`, authenticated via bearer token + optional HMAC body signature. Agent cards at `GET /.well-known/agent-card.json`.

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

Env-driven chain: `FEATHERLESS_API_KEY` → Featherless, fallback `OPENAI_API_KEY` → OpenAI. Uses `httpx` client, not the `openai` SDK package. Retries (2 attempts, 1.5s delay). Used by Reconciler (conflict suggestions) and LlmFactExtractor (fact extraction from files).

## Key Conventions

- Python 3.11+, ruff linting (`line-length = 100`)
- No typechecker configured
- `pyproject.toml` scripts entry: `mesh = "agents.runner:main"`
- No explicit test framework — single `test_integration.py` with auto-generated auth tokens
- `scripts/git_scraper.py` and `scripts/scraper.py` are standalone scrapers (not part of the agent mesh)
- `spikes/` contains feasibility validation experiments
- No FastAPI — pure Starlette + uvicorn
