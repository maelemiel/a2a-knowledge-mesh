# A2A Knowledge Mesh — Architecture

> 3 A2A agents. One job each: discover, store, reconcile.
> They find each other via A2A Agent Cards, coordinate through Band, and negotiate truth together.

## Principles

- **1 agent = 1 responsibility.** No agent does another's job.
- **A2A for transport.** Agent-to-Agent Protocol (Linux Foundation) — HTTP JSON-RPC between agents.
- **Band for visibility.** Band rooms show the jury real-time agent collaboration.
- **SQLite for facts.** No external DB. Each agent owns its store. Reconciliation compares across stores.
- **Source tracking.** Every fact carries a source_id + timestamp. No fact exists without provenance.

## Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Protocol | A2A (Agent-to-Agent) | Standard HTTP JSON-RPC. 150+ orgs. Framework-agnostic. |
| Coordination | Band REST API | Rooms, @mentions, persistent history. Required by hackathon. |
| Storage | SQLite | Zero infra. One file per agent. Easy to compare. |
| Inference | Featherless AI (BOA26) | Flat-rate open-source LLMs for resolution suggestions. |
| Fallback LLM | AI/ML API ($10 credits) | If Featherless rate-limited. |
| Language | Python 3.11+ | Fast to prototype, uv for speed. |
| HTTP | uvicorn + starlette | Lightweight ASGI. No FastAPI overhead needed for this scale. |

## Agents

```
Client ──► Registry ─── discover agents by skill
         │             GET  /.well-known/agent-card.json
         │             POST /a2a (register, list, find)
         │
         ├──► Keeper ─── store / recall facts
         │             POST /a2a (store-fact, recall, list-facts)
         │
         └──► Reconciler ─── detect conflicts, create Band room, resolve
                           POST /a2a (detect-conflict, resolve, status)
```

### Registry Agent (port 8765)

**Job:** Directory service. Knows what each agent can do.

- Every agent publishes an A2A Agent Card at `GET /.well-known/agent-card.json`
- Registry maintains a local registry of all known agents
- Clients call Registry to find "who can do X?"

**Skills:**
- `discover` — find agents by capability name
- `register` — agent self-registers with its card URL
- `list` — list all registered agents

**Data:** SQLite `registry.db`
```sql
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    card_url TEXT NOT NULL,
    skills TEXT NOT NULL,       -- JSON array
    url TEXT NOT NULL,          -- agent's base URL
    last_seen INTEGER NOT NULL  -- unix timestamp
);
```

### Keeper Agent (port 8766)

**Job:** Structured knowledge store. Facts in, facts out.

- Stores facts as (subject, predicate, object, source, timestamp) — RDF-lite
- Facts are immutable. Update = insert new version. Old versions stay for reconciliation.
- Supports recall by subject, by source, or by pattern.

**Skills:**
- `store-fact` — save a fact with source tracking
- `recall` — retrieve facts by subject, predicate, or source
- `list-facts` — paginated fact dump

**Data:** SQLite `keeper.db`
```sql
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT,
    timestamp INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_facts_subject ON facts(subject);
CREATE INDEX idx_facts_source ON facts(source_id);
```

### Reconciler Agent (port 8767)

**Job:** Find contradictions, negotiate truth.

- Reads facts from Keeper (or multiple Keepers)
- Detects contradictions: same (subject, predicate) but different object
- Creates a Band room for each conflict
- Posts conflict details with @mentions of relevant agents
- Records resolution when an agent (or human) resolves it

**Skills:**
- `detect-conflict` — scan for contradictory facts
- `resolve` — record a resolution (winner fact + reason)
- `status` — show open/closed conflicts

**Data:** SQLite `reconciler.db`
```sql
CREATE TABLE conflicts (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    fact_a_id INTEGER NOT NULL,
    fact_b_id INTEGER NOT NULL,
    band_room_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',  -- open | resolved
    resolution_fact_id INTEGER,
    resolution_reason TEXT,
    created_at INTEGER NOT NULL,
    resolved_at INTEGER
);

CREATE TABLE reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conflict_id TEXT NOT NULL REFERENCES conflicts(id),
    resolved_by TEXT NOT NULL,    -- agent id or 'human'
    resolution TEXT NOT NULL,     -- fact_a | fact_b | new
    reason TEXT,
    timestamp INTEGER NOT NULL
);
```

## Communication Flow

### A2A Protocol

Every agent implements the A2A protocol standard:

```
GET  /.well-known/agent-card.json  → Agent Card (capabilities)
POST /a2a                          → JSON-RPC call (method + params)
```

**Agent Card schema:**
```json
{
  "name": "Registry Agent",
  "description": "Directory service for A2A agents",
  "url": "http://localhost:8765",
  "skills": ["discover", "register", "list"],
  "version": "1.0.0",
  "authentication": null
}
```

**RPC call shape:**
```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "method": "discover",
  "params": {"skill": "store-fact"}
}
```

### Band Integration

Band is used for **conflict resolution visibility only**.

Flow:
1. Reconciler detects a contradiction
2. Creates a Band room: `POST /v2/agents/{id}/rooms` with title like `Conflict: project-ALLY (framework)`
3. Posts conflict details: conflicting facts, sources, timestamps
4. Sends @mention to both source agents: `@keeper-a` `@keeper-b`
5. When resolved, posts resolution + closes room

Band config lives in `.env`:
```
BAND_AGENT_ID=your-band-agent-id
BAND_API_KEY=your-band-api-key
```

## Port Map

| Agent | Port | Health Endpoint |
|-------|------|-----------------|
| Registry | 8765 | GET /health |
| Keeper | 8766 | GET /health |
| Reconciler | 8767 | GET /health |

## Directory Structure

```
a2a-knowledge-mesh/
├── agents/
│   ├── __init__.py
│   ├── base.py              # Base Agent class (Starlette, A2A handler, card, health)
│   ├── registry.py           # Registry agent + RegistryStore (SQLite)
│   ├── keeper.py             # Keeper agent + KeeperStore (SQLite)
│   └── reconciler.py         # Reconciler agent + ReconcilerStore + BandClient
│   └── runner.py             # CLI: start all 3 or single agent
├── protocols/
│   ├── __init__.py
│   └── a2a.py                # A2A dataclasses: AgentCard, A2ARequest, A2AResponse
├── band/
│   └── __init__.py           # (reserved for future Band SDK extraction)
├── lib/
│   └── band_client.py        # Standalone Band REST client (alternative impl)
├── mesh.py                   # Unified CLI (store, recall, discover, detect, status)
├── main.py                   # Entry stub
├── test_integration.py       # End-to-end demo script (7 steps)
├── spikes/                   # Feasibility spikes
├── docs/
│   ├── submission.md         # Hackathon submission text
│   ├── slides.md             # Slide deck
│   └── video_script.md       # Demo video script
├── .env.example
├── pyproject.toml
├── ARCHITECTURE.md           # This file
├── DESIGN.md                 # Protocol specs, card schemas, data contracts
└── README.md
```

> **Note:** Store classes (RegistryStore, KeeperStore, ReconcilerStore) are defined in the same module as their agent. No separate `db/` store files. BandClient is also inlined in `reconciler.py`. The `lib/band_client.py` is a standalone alternative.

## Non-Goals

- No multi-user auth. Demo only.
- No persistent Band webhooks. Poll-based detection.
- No distributed consensus. SQLite compare is sufficient for demo.
- No real LLM integration in v1. Resolution is rule-based; LLM is an enhancement path.
