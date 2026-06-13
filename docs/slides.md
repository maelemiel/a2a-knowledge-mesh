---
marp: true
theme: uncover
class:
  - lead
---

# A2A Knowledge Mesh

3 agents. Discover. Store. Reconcile.

**Band of Agents Hackathon** — June 2026
Track: Internal Enterprise Workflows

---

<!-- _class: lead -->
## The Problem

> "Which version is correct?"

- PDF on drive says "Python/Next.js"
- README on GitHub says "Python/FastAPI"
- Nobody knows which is true
- Context lost across tools, teams, time

Every team with 2+ sources has this problem.

---

## The Solution

3 specialized agents communicating via A2A Protocol + Band

| Agent | Job | Tech |
|-------|-----|------|
| **Registry** | Directory | A2A Agent Card |
| **Keeper** | Fact store | SQLite |
| **Reconciler** | Conflict resolution | Band room |

Standardized. Discoverable. Observable.

---

## Demo Flow

```
1. Client → Keeper:     store project:ALLY = Python/Next.js
2. Client → Keeper:     store project:ALLY = FastAPI       ← conflict!
3. Client → Reconciler:  detect-conflict
4. Reconciler:           finds 2 values for same key
5. Reconciler → Band:    creates room, posts conflict
6. Reconciler → Band:    posts resolution
```

All visible in Band chat room with @mentions.

---

## Architecture

```
┌─────────────┐    A2A Agent Cards     ┌─────────────┐
│   Registry  │◄─────────────────────►│   Keeper    │
│   Port 8765 │                        │   Port 8766 │
└──────┬──────┘                        └──────┬──────┘
       │                                      │
       │         A2A JSON-RPC                 │
       └────────────────┬─────────────────────┘
                        │
                   ┌────▼────┐
                   │Reconciler│
                   │Port 8767 │
                   └────┬────┘
                        │
                   ┌────▼────┐
                   │  Band   │
                   │  chat   │
                   │  room   │
                   └─────────┘
```

**Stack:** Python, Band REST API, SQLite, A2A Protocol

---

## Why This Matters

- **YC RFS #5** — Company Brain (Tom Blomfield)
- **YC RFS #13** — Software for Agents (Aaron Epstein)

| | Memory Store (YC P26) | Us |
|---|---|---|
| Sync | Passive (Slack/Gmail) | Active (Agent discovery) |
| Conflict | Last-write-wins | Negotiated resolution |
| Visibility | Black box | Band room with log |

**Collaborators:** AI/ML API, Featherless AI

---

## Try It

```
git clone https://github.com/maelemiel/a2a-knowledge-mesh
cd a2a-knowledge-mesh
uv sync
cp .env.example .env
uv run python agents/runner.py
uv run python test_integration.py
```

**Team:** Mael Perrigaud
**Links:** GitHub | Band | A2A Protocol
