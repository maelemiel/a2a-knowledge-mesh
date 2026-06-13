# A2A Knowledge Mesh

3 agents A2A qui forment un réseau de connaissance : Registry, Keeper, Reconciler.
Ils se découvrent via A2A Agent Cards, coordonnent via Band, et négocient la vérité.

**Hackathon :** Band of Agents (lablab.ai) — 12→19 juin 2026
**Track :** Internal Enterprise Workflows / Regulated & High-Stakes
**Stack :** Python, A2A Protocol, Band REST API, SQLite

## Architecture

```
Client → Registry (discover) → Keeper (store/recall) → Reconciler (conflict → Band room)
```

| Agent | Port | Skills |
|-------|------|--------|
| Registry | 8765 | discover, register |
| Keeper | 8766 | store-fact, recall |
| Reconciler | 8767 | detect-conflict, resolve |

## Quick Start

```bash
# Install
uv sync

# Config
cp .env.example .env
# Edit .env with your Band API key + LLM key

# Run all agents
uv run python -m agents.runner

# In another terminal, test
curl http://localhost:8765/.well-known/agent-card.json
curl http://localhost:8766/.well-known/agent-card.json
curl http://localhost:8767/.well-known/agent-card.json
```

## Spikes

Les 5 spikes de faisabilité sont dans `spikes/`. Chacun validé avant le build.

## RFS YC

Ce projet touche :
- **#5 Company Brain** — mémoire d'entreprise pour agents
- **#13 Software for Agents** — infrastructure agentique (A2A + Band)
