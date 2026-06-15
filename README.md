# A2A Knowledge Mesh

> 3 A2A agents that discover, store, and reconcile knowledge.
> They find each other via A2A Agent Cards, coordinate through Band, and negotiate truth together.

**Hackathon:** Band of Agents (lablab.ai) — June 12–19, 2026
**Track:** Internal Enterprise Workflows / Regulated & High-Stakes
**Stack:** Python, A2A Protocol, Band REST API, SQLite

## Architecture

```
Client ──► Registry ─── discover agents by skill
         ├──► Keeper ─── store / recall facts
         └──► Reconciler ─── detect conflicts, create Band room, resolve
```

| Agent | Port | Skills |
|-------|------|--------|
| Registry | 8765 | discover, register, list |
| Keeper | 8766 | store-fact, recall, list-facts |
| Reconciler | 8767 | detect-conflict, resolve, status |

## Quick Start

```bash
git clone https://github.com/maelemiel/a2a-knowledge-mesh.git
cd a2a-knowledge-mesh
uv sync

# Copy and configure (Band credentials optional — demo works without)
cp .env.example .env

# Run all 3 agents
uv run python -m agents.runner
```

In another terminal, test the full flow:

```bash
# Clean + run demo (7 steps)
uv run python test_integration.py

# Or use the CLI
uv run python mesh.py status
uv run python mesh.py store subject=project-ALLY predicate=framework object=Next.js source=docs-repo
uv run python mesh.py recall project-ALLY
uv run python mesh.py detect
```

Expected output: 7 steps — register keeper → discover by skill → store facts → recall → detect conflict → resolve → status.

## Demo Flow

1. **Register** — Keeper agent registers with Registry
2. **Discover** — Find Keeper by skill `store-fact`
3. **Store** — Save facts from different sources (with conflicting values)
4. **Detect** — Reconciler scans Keeper, finds contradictions
5. **Resolve** — Pick the winning fact, record the decision
6. **Verify** — Check open/closed conflict status

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — Stack, agents, port map, data model
- [DESIGN.md](DESIGN.md) — Agent cards, RPC method signatures, contracts

## Spikes

Feasibility spikes in [`spikes/`](spikes/) validated each technology before building the real agents.

## YC RFS Alignment

Touches 2 YC Requests for Startups (Summer 2026):

- **#5 Company Brain** (Tom Blomfield) — shared knowledge layer for agents
- **#13 Software for Agents** (Aaron Epstein) — agent-native infrastructure

**Competing solution:** Memory Store (YC P26) does passive sync (Slack/Gmail → memory). We do active reconciliation (A2A discovery → conflict detection → Band resolution).

## Tech Partners

- **AI/ML API** — $10 credits for LLM-powered resolution suggestions
- **Featherless AI** — $25 credits (code `BOA26`), flat-rate open-source inference
