# A2A Knowledge Mesh

> 5 Band agents. One job each: discover, store, reconcile.
> They run on your machine, talk through Band WebSocket, and negotiate truth together.

**Hackathon:** Band of Agents (lablab.ai) — June 12–19, 2026
**Track:** Internal Enterprise Workflows / Regulated & High-Stakes
**Stack:** Python, Band SDK, SQLite

## Concept

When different agents store conflicting facts ("project X uses Python 3.13" vs "project X uses Node 22"), who's right? The mesh detects contradictions via SQL JOIN, asks an LLM to suggest a winner, and lets a human resolve it in Band.

```
   ┌─────────┐  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌────────┐
   │ Scraper │  │ Registry │  │  Keeper    │  │Reconciler│  │ Bridge │
   │ (Band)  │  │ (Band)   │  │ (Band)     │  │ (Band)   │  │ (REST) │
   └────┬────┘  └────┬─────┘  └─────┬──────┘  └────┬─────┘  └───┬────┘
        │            │              │             │           │
        └────────────┴──── Band WebSocket ────────┴───────────┘
                                 │
                              SQLite (data/*.db)
                                 │
                         Dashboard (stdlib HTTP)
```

| Agent | Role | Commands |
|-------|------|----------|
| **Scraper** | Scans local repos → extracts facts via LLM → sends to Keeper | `scan self`, `scan <path>`, `status` |
| **Registry** | Directory of agents and their skills | `register`, `discover`, `list` |
| **Keeper** | SQLite fact store, auto-detects conflicts on insert | `store`, `recall`, `list`, `detect` |
| **Reconciler** | AI conflict analysis, scoring, auto-resolve | `detect`, `status`, `resolve` |
| **Bridge** | Observes room messages, exposes REST API for dashboard | *(passive)* |

## Quick Demo (30 seconds, no Band needed)

```bash
git clone https://github.com/maelemiel/a2a-knowledge-mesh.git
cd a2a-knowledge-mesh
uv sync

# Generate a quick demo database with sample data
uv run python scripts/quick_demo.py
```

This creates fake facts in SQLite, detects a conflict, resolves it, and
launches a standalone dashboard. Open http://localhost:8766 — no Band
account, no API keys, no agents running.

## Full Setup (with Band)

### 1. Create Band agents

1. Go to [app.band.ai](https://app.band.ai) → Settings → Agents → Create 5 **Remote Agents**:
   - `scraper`, `keeper`, `reconciler`, `registry`, `bridge`
2. Copy each agent's UUID + API key
3. Create one **room** and add all 5 agents + yourself
4. Copy the room ID

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — fill in:
- `BAND_*_ID` + `BAND_*_KEY` for each of the 5 agents
- `BAND_ROOM_ID` (the shared room)
- `BAND_USER_HANDLE` (your Band handle, e.g. `mael2perso`)
- At least one LLM key (Featherless or OpenAI)

> 💡 `run_mesh.sh` validates all required vars on startup and tells you
> exactly which ones are missing.

### 3. Launch

```bash
bash scripts/run_mesh.sh
```

Opens:
- **Dashboard** → http://localhost:8766 — live timeline + metrics + agents
- **Band room** → type `@keeper list` to see agents responding

### 4. Try it in Band

```text
@keeper store subject=project-ALLY predicate=framework object=Next.js source=docs-repo
@keeper store subject=project-ALLY predicate=framework object=React source=code-repo
@reconciler detect
@reconciler status
@scraper scan self
```

## Offline Mode

No Band internet? No problem:

```bash
uv run python scripts/quick_demo.py --serve
```

Same dashboard with pre-loaded facts and conflicts. Works anywhere.

## Architecture

Each agent is a Python `SimpleAdapter` connected to Band via WebSocket.
They never expose HTTP — Band IS the mesh. The only HTTP processes are:

- **Bridge** (port 8765) — REST API that mirrors room events for the dashboard
- **Dashboard** (port 8766) — stdlib HTTP server serving index.html

### Mention handling

Band delivers messages with `@Display Name` or `@[[UUID]]` prefixes.
The base class (`BandAgent`) strips all leading mentions before dispatching
commands — so `@Maël Perrigaud/scraper scan self` becomes `scan self`.

### Agent-to-agent routing

When an agent needs another agent, it @mentions them explicitly. Replies
always mention the human user to prevent infinite agent↔agent loops.

### Self-registration

On joining the HQ room, each agent auto-registers with Registry via @mention.
Registry does not self-register (Band rejects `cannot_mention_self`).

## Key Features

- **SQL JOIN conflict detection** — O(n log n), reads facts grouped by
  (subject, predicate) with COUNT(DISTINCT object) > 1
- **LLM-powered resolution** — Reconciler asks the LLM to pick a winner
  and explain why
- **Mention stripping** — Seamless handling of Unicode names with spaces
- **Dashboard** — Live SQLite metrics, agent directory, command cheatsheet,
  one-click reset
- **Resilient** — Stale replay dedup (15s grace), anti-loop protection,
  empty-message guard

## Project Structure

```
├── agents/
│   ├── band_agent.py          # Base class (BandAgent)
│   ├── bridge_agent.py        # REST bridge for dashboard
│   ├── keeper_band.py         # Keeper Band agent
│   ├── keeper.py              # KeeperStore (SQLite schema)
│   ├── reconciler_band.py     # Reconciler Band agent
│   ├── reconciler.py          # ReconcilerStore + conflict engine
│   ├── registry_band.py       # Registry Band agent
│   ├── registry.py            # RegistryStore
│   ├── scraper_band.py        # Scraper Band agent
│   ├── scraper_service.py     # LLM-based repo scanning
│   ├── provider.py            # LLM provider abstraction
│   └── auth.py                # HMAC signing, token validation
├── dashboard/
│   ├── server.py              # stdlib HTTP server
│   └── index.html             # Dashboard UI
├── data/                      # SQLite databases (gitignored)
├── scripts/
│   ├── run_mesh.sh            # Launch all 5 agents + dashboard
│   └── quick_demo.py          # 30-second demo, no Band needed
├── spikes/                    # Feasibility prototypes
├── mesh.py                    # CLI for DB queries
├── DEMO.md                    # Demo script for jury
└── README.md
```

## YC RFS Alignment

Touches 2 YC Requests for Startups (Summer 2026):

- **#5 Company Brain** (Tom Blomfield) — shared knowledge layer for agents
- **#13 Software for Agents** (Aaron Epstein) — agent-native infrastructure

**Competing solution:** Memory Store (YC P26) does passive sync
(Slack/Gmail → memory). We do active reconciliation (A2A discovery →
SQL conflict detection → Band → human resolution).
