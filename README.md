# A2A Knowledge Mesh

> 3 A2A agents collaborating through Band to maintain shared knowledge.
> Discover. Store. Reconcile.

**Hackathon:** Band of Agents (lablab.ai) — June 12–19, 2026
**Track:** Internal Enterprise Workflows / Regulated & High-Stakes
**Stack:** Python, A2A Protocol, Band REST API, SQLite

## Architecture

```
Client → Registry (find agents by skill)
       → Keeper (store / recall facts)
       → Reconciler (detect conflicts, create Band room, resolve)
```

| Agent | Port | Skills |
|-------|------|--------|
| Registry | 8765 | discover, register |
| Keeper | 8766 | store-fact, recall |
| Reconciler | 8767 | detect-conflict, resolve, status |

## Quick Start

```bash
# Install
git clone https://github.com/maelemiel/a2a-knowledge-mesh.git
cd a2a-knowledge-mesh
uv sync

# Config
cp .env.example .env
# Add your Band API key (create an agent at https://app.band.ai/agents)

# Run all 3 agents
uv run python agents/runner.py

# In another terminal, test the flow
uv run python test_integration.py
```

## Demo Flow

1. **Store a fact** → Keeper Agent saves it to SQLite
2. **Discover** → Registry Agent finds agents by capability
3. **Detect conflict** → Reconciler compares stores, finds contradictions
4. **Resolve** → Reconciler creates a Band room, posts conflict + resolution

## Spikes

5 feasibility spikes in `spikes/`. Each validated before building the real agents.

## YC RFS Alignment

This project touches 2 YC Requests for Startups (Summer 2026):

- **#5 Company Brain** (Tom Blomfield) — shared knowledge layer for agents
- **#13 Software for Agents** (Aaron Epstein) — agent-native infrastructure (A2A + Band)

**Competing solution:** Memory Store (YC P26) does passive sync (Slack/Gmail → memory). We do active reconciliation (A2A discovery → conflict detection → Band resolution).

## Tech Partners

- **AI/ML API** — $10 credits, used for LLM-powered resolution suggestions
- **Featherless AI** — $25 credits (code `BOA26`), flat-rate open-source inference
