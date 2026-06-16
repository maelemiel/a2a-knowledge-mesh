# A2A Knowledge Mesh — Design

> Agent Card schemas, RPC methods, and data contracts.

## Agent Cards

### Registry Agent (`GET /.well-known/agent-card.json`)

```json
{
  "name": "Registry Agent",
  "description": "Directory service for A2A agents",
  "url": "http://localhost:8765",
  "skills": ["discover", "register", "list", "unregister"],
  "version": "1.0.0",
  "authentication": {"schemes": [{"type": "bearer"}]}
}
```

### Keeper Agent (`GET /.well-known/agent-card.json`)

```json
{
  "name": "Keeper Agent",
  "description": "Structured knowledge store with source tracking",
  "url": "http://localhost:8766",
  "skills": ["store-fact", "store-facts-batch", "recall", "list-facts", "detect-conflicts", "get-fact"],
  "version": "1.0.0",
  "authentication": {"schemes": [{"type": "bearer"}]}
}
```

### Reconciler Agent (`GET /.well-known/agent-card.json`)

```json
{
  "name": "Reconciler Agent",
  "description": "Detects contradictory facts and resolves them via Band",
  "url": "http://localhost:8767",
  "skills": ["detect-conflict", "resolve", "status", "open-conflicts"],
  "version": "1.0.0",
  "authentication": {"schemes": [{"type": "bearer"}]}
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
| `store-fact` | `{subject, predicate, object, source_id?, source_url?}` | `{id, subject, predicate, object}` |
| `store-facts-batch` | `{facts: [{subject, predicate, object, source_id?, source_url?}]}` | `{facts: [{id, subject, predicate, object}]}` |
| `recall` | `{subject?, source_id?}` | `{facts: [{id, subject, predicate, object, source_id, source_url, timestamp, version}]}` |
| `list-facts` | `{limit?, offset?}` | `{facts: [...]}` |
| `detect-conflicts` | `{limit?, offset?}` | `{conflicts: [{subject, predicate, fact_a_id, fact_b_id, source_a, source_b, ...}]}` |
| `get-fact` | `{id}` | `{fact: {...}}` |

Conflict detection is handled server-side via SQL JOIN — no O(n²) memory scan.

### Reconciler

| Method | Params | Returns |
|--------|--------|---------|
| `detect-conflict` | `{keeper_url?}` | `{conflicts: [{conflict_id, subject, predicate, ai_suggested_fact_id, ai_reason}], count}` |
| `resolve` | `{conflict_id, resolution_fact_id, reason?}` | `{conflict_id, status}` |
| `status` | `{}` | `{open: [...], all: [...]}` |
| `open-conflicts` | `{}` | `{conflicts: [...]}` |

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
GET  /.well-known/agent-card.json  → Agent Card (agent metadata + skills, auth scheme)
GET  /health                        → {"status": "UP|DEGRADED", "agent": "...", "checks": [...]}
POST /a2a                           → JSON-RPC 2.0 (method dispatch, requires bearer auth)
POST /band-webhook                  → [Reconciler only] Push resolution from Band
```

All non-public endpoints require a bearer token in the ``Authorization`` header.
Tokens are configured via environment variables (``A2A_REGISTRY_TOKEN``,
``A2A_KEEPER_TOKEN``, ``A2A_RECONCILER_TOKEN``, or ``A2A_MASTER_TOKEN``).

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

Errors return spec-compliant codes:

| HTTP Status | JSON-RPC Code | Meaning |
|-------------|---------------|---------|
| 400 | -32700 | Parse error (invalid JSON) |
| 400 | -32600 | Invalid Request (missing `method`, wrong `jsonrpc`) |
| 404 | -32601 | Method not found |
| 422 | -32602 | Invalid params (type/validation error) |
| 500 | -32603 | Internal error |
| 401 | -32001 | Missing auth token |
| 403 | -32003 | Invalid or expired token |

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
