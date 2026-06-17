# A2A Knowledge Mesh

> Band-native multi-agent workflow for enterprise knowledge reconciliation.
> Agents scrape, store, detect contradictions, hand off review, and keep humans in the loop through Band.

**Hackathon:** Band of Agents Hackathon  
**Primary track:** Regulated & High-Stakes Workflows  
**Secondary track:** Internal Enterprise Workflows  
**Stack:** Python, Band SDK, SQLite, Featherless/OpenAI-compatible HTTP APIs

## What It Solves

Enterprise knowledge drifts. Code, docs, runbooks, tickets, and human notes often disagree. This project turns that drift into a coordinated workflow:

1. **Scraper** extracts structured facts from a repository.
2. **Keeper** stores facts with provenance and detects contradictory claims.
3. **Reconciler** reviews conflicts with LLM scoring, root-cause analysis, and resolution tracking.
4. **Registry** records agent capabilities and can orchestrate a clean demo reset.
5. **Bridge** mirrors Band room activity and live SQLite state to a dashboard.

Band is the collaboration layer: agents pass structured context, mention the next specialist, and make the handoff visible in the shared room.

## Band-Native Architecture

```
Band Room
  |
  |  slurp git <repo>
  v
Scraper ── structured facts ──► Keeper
                                  |
                                  | conflict.detected handoff
                                  v
                              Reconciler ──► AI suggestion / resolution
                                  |
                                  v
                              Dashboard Bridge
```

| Agent | Module | Role |
|---|---|---|
| Scraper | `agents/scraper_band.py` | Parses project files and sends fact batches to Keeper |
| Keeper | `agents/keeper_band.py` | SQLite fact store, SQL conflict detection, Reconciler handoff |
| Reconciler | `agents/reconciler_band.py` | LLM conflict review, dedupe, resolution history |
| Registry | `agents/registry_band.py` | Agent directory, discovery, demo reset orchestration |
| Bridge | `agents/bridge_agent.py` | WebSocket listener + local dashboard metrics API |

The older HTTP A2A agents are kept for local protocol tests, but the hackathon demo uses the Band-native agents above.

## Setup

```bash
uv sync
cp .env.example .env
```

Create 5 Band agents and add all of them to the same Band room:

- Scraper
- Keeper
- Reconciler
- Registry
- Bridge

Fill `.env` with the room ID, agent IDs, API keys, and your Band handle. Keep `.env` private.

## Run

```bash
bash scripts/run_mesh.sh
```

Dashboard:

```text
http://localhost:8776
```

If ports are busy:

```bash
lsof -i :8776 -i :8775
kill $(lsof -ti :8776 -ti :8775)
```

## Judge Demo Flow

In Band:

```text
@Registry reset-demo
```

Then run the full workflow:

```text
@Scraper slurp git /home/eliott/a2a-knowledge-mesh
```

Expected flow:

1. Scraper scans files and sends `store-batch` to Keeper.
2. Keeper stores facts and runs SQL conflict detection.
3. Keeper posts structured `conflict.detected` context and mentions Reconciler.
4. Reconciler scores the conflict, suggests a winner, explains root cause, and records state.
5. Dashboard updates live: messages, facts, open conflicts, resolved conflicts.

Manual mini-demo:

```text
@Registry reset-demo
@Keeper store subject=project-ALLY predicate=framework object=Next.js source=docs
@Keeper store subject=project-ALLY predicate=framework object=FastAPI source=code
@Keeper detect
@Reconciler status
```

After the two stores, dashboard should show:

```text
Facts Stored: 2
Conflicts: 1
Resolved: 0
```

After resolving:

```text
@Reconciler resolve <conflict_id> <fact_id> docs_updated
```

Dashboard should show:

```text
Conflicts: 0
Resolved: 1
```

## Why It Matches The Hackathon

- **At least 3 collaborating agents:** Scraper, Keeper, Reconciler; Registry and Bridge add discovery and observability.
- **Meaningful Band usage:** facts, conflict handoffs, mentions, status, and human review happen inside Band.
- **Enterprise workflow:** detects drift between enterprise knowledge sources and produces traceable resolution.
- **Originality:** not a chatbot; it is a coordinated knowledge-control workflow with structured context and provenance.

## Tests

Unit tests:

```bash
uv run python -m unittest -v test_unit.py
```

If local `uv` is broken by Snap, use:

```bash
.venv/bin/python -m unittest -v test_unit.py
```

HTTP A2A integration test, legacy/local path:

```bash
cp -a data data.backup.$(date +%s)
uv run python test_integration.py
```

## Safety

Do not commit `.env`. Rotate Band and model-provider keys if they are exposed in logs, screenshots, chats, or demos.
