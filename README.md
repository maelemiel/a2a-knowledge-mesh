# A2A Knowledge Mesh

> 3 A2A agents that discover, store, and reconcile knowledge.
> They find each other via A2A Agent Cards, coordinate through Band, and negotiate truth together.

**Hackathon:** Band of Agents (lablab.ai) — June 12–19, 2026
**Track:** Internal Enterprise Workflows / Regulated & High-Stakes
**Stack:** Python, A2A Protocol, Band API, SQLite, Pydantic

## Architecture

```
                  ┌──────────┐
  CLI / test ────►│ Registry │  discover agents by skill
                  ├──────────┤
User ──► mesh CLI─►│  Keeper  │  store / recall / batch-insert facts
                  ├──────────────┤
                  │ Reconciler │  SQL conflict detection → AI suggestion → Band room → webhook
                  └──────────────┘
```

| Agent | Port | Skills |
|-------|------|--------|
| Registry | 8765 | discover, register, unregister, list |
| Keeper | 8766 | store-fact, store-facts-batch, recall, list-facts, detect-conflicts, get-fact |
| Reconciler | 8767 | detect-conflict, resolve, status, open-conflicts |

## Quick Start

```bash
git clone https://github.com/maelemiel/a2a-knowledge-mesh.git
cd a2a-knowledge-mesh
uv sync

# Generate auth tokens & configure
cp .env.example .env
# Fill in: A2A_REGISTRY_TOKEN, A2A_KEEPER_TOKEN, A2A_RECONCILER_TOKEN
# Generate with:  openssl rand -hex 32

# Run all 3 agents
uv run python -m agents.runner
# Or via the CLI script entrypoint
uv run mesh-runner
```

In another terminal, test the full flow:

```bash
# All agents must be running with tokens set in environment

# E2E integration test (8 steps, uses auto-generated tokens)
uv run python test_integration.py

# Or use the Web Dashboard
After starting the agents, open your browser and navigate to:
[http://localhost:8767/dashboard](http://localhost:8767/dashboard)

This dashboard provides a premium interactive interface featuring:
- **Interactive SVG Topology Graph**: pulsing node flows indicating agent health and live conflict alerts.
- **Side-by-Side Conflict Comparison**: direct visibility into Fact A (e.g., docs-repo) vs Fact B (e.g., code-repo) contradictions.
- **AI Recommendation Engine**: clear view of the winner fact selected by the LLM along with its detailed reasoning.
- **One-Click Resolvers**: instant manual or AI-driven conflict resolution.
- **Ingested Fact Search**: live-filterable table explorer of all facts stored in the Keeper agent.

# Or use the CLI
uv run python mesh.py status
uv run python mesh.py store subject=project-ALLY predicate=framework object=Next.js source=docs-repo
uv run python mesh.py recall project-ALLY
uv run python mesh.py detect
uv run python mesh.py ingest     # auto-scrape pyproject.toml + .env.example
```

**Expected output:** 8 steps — register → discover → store facts → recall → detect conflicts (SQL JOIN) → resolve → verify status → JSON-RPC error compliance.

## Authentication

All agents use bearer token auth on `/a2a` endpoints. Tokens are set via env vars:

| Variable | Agent |
|----------|-------|
| `A2A_REGISTRY_TOKEN` | Registry |
| `A2A_KEEPER_TOKEN` | Keeper |
| `A2A_RECONCILER_TOKEN` | Reconciler |
| `A2A_MASTER_TOKEN` | CLI / cross-agent fallback |

Public endpoints (health, agent card) don't require auth.

## Demo Flow

1. **Register** — Keeper agent registers with Registry (requires bearer token)
2. **Discover** — Find Keeper by skill `store-fact`
3. **Store** — Save facts from different sources (conflicting values)
4. **Detect** — Reconciler calls Keeper's SQL JOIN `detect-conflicts`, gets contradictions, runs AI suggestion
5. **Resolve** — Pick the winning fact, record the decision
6. **Ingest** — Auto-scrape `pyproject.toml` / `.env.example` into facts
7. **Band Webhook** — Push-based resolution when human replies in Band room

## Key Features

- **SQL JOIN conflict detection** — O(n log n) instead of O(n²) in-memory scan
- **Pydantic validation** — All RPC params validated with strict types
- **JSON-RPC 2.0 spec** — Proper error codes (-32700, -32601, -32602, -32603), `jsonrpc` field validation
- **ASGI auth middleware** — Bearer token per role, master token fallback
- **Async I/O** — httpx.AsyncClient throughout, Band retries with exponential backoff
- **Resilient LLM parser** — Handles markdown fences, trailing commas, truncated JSON
- **Ingestion scraper** — Extensible `Scraper` ABC with built-in pyproject.toml/.env scrapers
- **Health checks** — Dependency-aware probes (DB, peer agents, Band)

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — Stack, agents, port map, data model, DB schemas
- [DESIGN.md](DESIGN.md) — Agent cards, RPC method signatures, contracts, error codes

## Spikes

Feasibility spikes in [`spikes/`](spikes/) validated each technology before building the real agents.

## YC RFS Alignment

Touches 2 YC Requests for Startups (Summer 2026):

- **#5 Company Brain** (Tom Blomfield) — shared knowledge layer for agents
- **#13 Software for Agents** (Aaron Epstein) — agent-native infrastructure

**Competing solution:** Memory Store (YC P26) does passive sync (Slack/Gmail → memory). We do active reconciliation (A2A discovery → SQL conflict detection → Band resolution).

## Tech Partners

- **AI/ML API** — $10 credits for LLM-powered resolution suggestions
- **Featherless AI** — $25 credits (code `BOA26`), flat-rate open-source inference
