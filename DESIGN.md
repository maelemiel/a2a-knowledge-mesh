# A2A Knowledge Mesh — Design

> Agent Card schemas, RPC methods, and data contracts.

## Agent Cards

### Registry Agent (`GET /.well-known/agent-card.json`)

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

### Keeper Agent (`GET /.well-known/agent-card.json`)

```json
{
  "name": "Keeper Agent",
  "description": "Structured knowledge store with source tracking",
  "url": "http://localhost:8766",
  "skills": ["store-fact", "recall", "list-facts"],
  "version": "1.0.0",
  "authentication": null
}
```

### Reconciler Agent (`GET /.well-known/agent-card.json`)

```json
{
  "name": "Reconciler Agent",
  "description": "Detects contradictory facts and resolves them via Band",
  "url": "http://localhost:8767",
  "skills": ["detect-conflict", "resolve", "status"],
  "version": "1.0.0",
  "authentication": null
}
```

## RPC Methods

### Registry

| Method | Params | Returns |
|--------|--------|---------|
| `register` | `{agent_id, name, card_url, skills[], url}` | `{status, agent_id}` |
| `discover` | `{skill}` | `{agents: [{id, name, card_url, skills, url}]}` |
| `list` | `{}` | `{agents: [...]}` |

### Keeper

| Method | Params | Returns |
|--------|--------|---------|
| `store-fact` | `{subject, predicate, object, source_id, source_url?}` | `{id, subject, predicate, object}` |
| `recall` | `{subject?, source_id?}` | `{facts: [{id, subject, predicate, object, source_id, source_url, timestamp, version}]}` |
| `list-facts` | `{limit?, offset?}` | `{facts: [...]}` |

### Reconciler

| Method | Params | Returns |
|--------|--------|---------|
| `detect-conflict` | `{keeper_url?}` | `{conflicts: [{conflict_id, subject, predicate}], count}` |
| `resolve` | `{conflict_id, resolution_fact_id, reason?}` | `{conflict_id, status}` |
| `status` | `{}` | `{open: [...], all: [...]}` |

## Data Contracts

### Fact (Keeper Store)

| Field | Type | Description |
|-------|------|-------------|
| id | int | Auto-increment |
| subject | string | Entity (e.g. "project-ALLY") |
| predicate | string | Attribute (e.g. "framework") |
| object | string | Value (e.g. "Next.js") |
| source_id | string | Origin identifier |
| source_url | string? | Link to source |
| timestamp | int | Unix epoch seconds |
| version | int | Monotonic per (subject, predicate, source_id) |

### Conflict (Reconciler Store)

| Field | Type | Description |
|-------|------|-------------|
| id | string | UUID4 (first 8 chars) |
| subject | string | Conflicting entity |
| predicate | string | Conflicting attribute |
| fact_a_id | int | Fact from source A |
| fact_b_id | int | Fact from source B |
| source_a | string | Source A identifier |
| source_b | string | Source B identifier |
| band_room_id | string? | Band room for this conflict |
| status | string | `open` or `resolved` |
| resolution_fact_id | int? | Winning fact's ID |
| resolution_reason | string? | Why this fact wins |

## A2A Transport

Every agent exposes:

```
GET  /.well-known/agent-card.json  → Agent Card (agent metadata + skills)
GET  /health                        → {"status": "ok", "agent": "..."}
POST /a2a                           → JSON-RPC 2.0 (method dispatch)
```

The `/a2a` endpoint accepts:
```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "method": "<skill-name>",
  "params": {...}
}
```

And returns:
```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "result": {...}
}
```

Errors return HTTP 400/500 with:
```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "error": {"code": -32000, "message": "..."}
}
```

## Health Checks

Every agent exposes `GET /health` returning:
```json
{"status": "ok", "agent": "Registry Agent"}
```

Port assignments:

| Agent | Port | Health |
|-------|------|--------|
| Registry | 8765 | GET /health |
| Keeper | 8766 | GET /health |
| Reconciler | 8767 | GET /health |
