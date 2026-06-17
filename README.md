# A2A Knowledge Mesh

Band-native multi-agent knowledge mesh for enterprise knowledge drift detection.

The demo shows five specialized agents collaborating inside a Band room:

| Agent | Role |
| --- | --- |
| Scraper | Scans a repo with an LLM and extracts structured facts |
| Keeper | Stores facts in SQLite and detects contradictions |
| Reconciler | Reviews conflicts, scores severity, suggests or records resolutions |
| Registry | Maintains an agent directory and can reset the demo |
| Bridge | Mirrors Band activity and local DB state to the dashboard |

Band is the collaboration layer: agents mention each other, hand off structured context, register capabilities, and keep the workflow visible.

## Setup

```bash
uv sync
cp .env.example .env
```

Fill `.env` with your Band agent IDs/keys and room ID. Each agent must be added to the same Band room.

Recommended ports:

```env
BRIDGE_PORT=8775
DASHBOARD_PORT=8776
BRIDGE_URL=http://127.0.0.1:8775
```

## Run

```bash
bash scripts/run_mesh.sh
```

Open:

```text
http://localhost:8776
```

If ports are already in use:

```bash
lsof -i :8776 -i :8775
kill $(lsof -ti :8776 -ti :8775) 2>/dev/null || true
```

## Demo Commands

In the Band room:

```text
@Keeper reset-demo
@Keeper store subject=project-ALLY predicate=framework object=Next.js source=docs
@Keeper store subject=project-ALLY predicate=framework object=FastAPI source=code
@Keeper detect
@Reconciler detect
@Reconciler status
@Reconciler resolve <conflict_id> <fact_id> code is source of truth
```

For repo scanning:

```text
@Scraper scan self
@Scraper status
```

## Dashboard

The dashboard shows:

- live timeline from Band and local SQLite state
- persistent audit history in `data/bridge.db`
- fact/conflict/resolution counters from SQLite
- registered agents from `registry.db`
- reset button for local demo databases

The live timeline is intentionally backed by both Band WebSocket events and local DB polling, so facts, conflicts, and resolutions still appear even if a Band event is missed.

## Tests

```bash
.venv/bin/python -m unittest test_unit.py
```

or:

```bash
uv run python -m unittest test_unit.py
```

`test_integration.py` remains the legacy HTTP A2A end-to-end path. The hackathon demo path is the Band-native mesh launched with `scripts/run_mesh.sh`.
