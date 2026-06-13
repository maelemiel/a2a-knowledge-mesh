# A2A Knowledge Mesh

> 3 agents. One goal: keep your facts straight.

**Registry** — directory of agents and their skills  
**Keeper** — stores facts in SQLite  
**Reconciler** — detects contradictions, creates Band rooms  

---

## Setup

```bash
git clone https://github.com/maelemiel/a2a-knowledge-mesh
cd a2a-knowledge-mesh
uv sync
```

---

## Start

```bash
# Start all 3 agents
uv run python agents/runner.py
```

In another terminal:

```bash
# Verify all agents are online
uv run python mesh.py status
```

Expected output:
```
✅ registry (port 8765) — discover, register
✅ keeper (port 8766) — store-fact, recall
✅ reconciler (port 8767) — detect-conflict, resolve, status
```

---

## Tutorial: Store → Conflict → Detect → Resolve

### Step 1: Store a fact

```bash
uv run python mesh.py store "project:ALLY=Python/Next.js store=live"
```

Keeper saves it to SQLite. Output: `Stored: project:ALLY = Python/Next.js (store: live)`

### Step 2: See what's stored

```bash
uv run python mesh.py recall
```

Output:
```
Facts:
  [live] project:ALLY = Python/Next.js
```

### Step 3: Store a contradictory fact

```bash
uv run python mesh.py store "project:ALLY=Python/FastAPI store=staging"
```

Now you have **two sources** saying different things. Same key (`project:ALLY`), different values.

### Step 4: Detect the conflict

```bash
uv run python mesh.py detect
```

The Reconciler:
1. Asks Keeper for all facts
2. Populates its own DB
3. Runs `SELECT ... JOIN WHERE value != value`
4. Finds contradictions

Output:
```
Conflicts detected: 1
  1. project:ALLY: [live]=Python/Next.js vs [staging]=Python/FastAPI
```

### Step 5: Resolve

```bash
uv run python mesh.py resolve project:ALLY live
```

The Reconciler sets `live` as the canonical source. All stores are updated.

### Step 6: Auto-watch (bonus)

```bash
uv run python mesh.py watch 5
```

Scans for new conflicts every 5 seconds. Press Ctrl+C to stop.

---

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                      Your Terminal                   │
│  mesh store / mesh detect / mesh resolve             │
└────────┬──────────────┬──────────────────────┬──────┘
         │              │                      │
    ┌────▼────┐   ┌────▼────┐           ┌────▼────┐
    │ Registry │   │ Keeper  │           │Reconciler│
    │ Port 8765 │   │Port 8766│           │Port 8767 │
    │ discover  │   │store-fact│          │detect-   │
    │ register  │   │recall   │          │conflict  │
    └──────────┘   │SQLite DB│           │resolve   │
                   └─────────┘           │status    │
                                         └────┬────┘
                                              │
                                         ┌────▼────┐
                                         │  Band   │
                                         │  room   │
                                         │  (chat  │
                                         │  with   │
                                         │@mentions)│
                                         └─────────┘
```

**Every agent communicates via A2A Protocol** — the standard from Linux Foundation (150+ orgs). Each publishes its capabilities in an Agent Card (`/.well-known/agent-card.json`). When one agent needs another, it reads their card and calls them directly over HTTP JSON-RPC.

**Band is used for visibility.** The Reconciler creates a chat room with @mentions so humans and agents can see conflicts appear, discuss them, and confirm resolutions.

---

## API Reference

### Agent Cards

```bash
curl http://localhost:8765/.well-known/agent-card.json
curl http://localhost:8766/.well-known/agent-card.json
curl http://localhost:8767/.well-known/agent-card.json
```

### Direct A2A (without mesh CLI)

```bash
# Store
curl -X POST http://localhost:8766/a2a \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"parts":[{"text":"store-fact key=X value=Y store=Z"}]}}}'

# Recall
curl -X POST http://localhost:8766/a2a \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"parts":[{"text":"recall all"}]}}}'

# Detect conflicts
curl -X POST http://localhost:8767/a2a \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"parts":[{"text":"detect-conflict"}]}}}'

# Discover agents by skill
curl -X POST http://localhost:8765/a2a \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"parts":[{"text":"discover skills:store-fact"}]}}}'
```

---

## Project Structure

```
hackathon-band-of-agents/
├── agents/
│   ├── registry.py       # Directory agent
│   ├── keeper.py         # Fact storage agent
│   ├── reconciler.py     # Conflict detection + Band room
│   └── runner.py         # Starts all 3
├── lib/
│   ├── a2a_server.py     # Shared HTTP server (Agent Card + JSON-RPC)
│   ├── band_client.py    # Band REST API wrapper
│   └── conflict.py       # SQLite conflict detection logic
├── mesh.py               # Unified CLI
├── test_integration.py   # Integration test
├── spikes/               # Feasibility prototypes
│   ├── 001-band-sdk/      # Band SDK connectivity
│   ├── 002-a2a-agent-card/ # A2A Agent Card server
│   ├── 003-a2a-discovery/  # Multi-agent A2A discovery
│   ├── 004-conflict-detection/ # SQLite conflict detection
│   ├── 005-band-resolution/    # Band rooms + messages
│   └── 006-featherless-qwen/   # Featherless AI API
└── docs/
    ├── submission.md     # Hackathon submission text
    ├── slides.md         # Slide deck (Marp format)
    └── video_script.md   # 5 min demo video script
```

---

## Credits

Built for [Band of Agents Hackathon](https://lablab.ai/ai-hackathons/band-of-agents-hackathon) (June 2026).

**Partners:** AI/ML API ($10 credits), Featherless AI ($25 via `BOA26`)
**Protocol:** A2A (Agent-to-Agent) by Linux Foundation
**Platform:** Band by Thenvoi
