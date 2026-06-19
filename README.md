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
http://localhost:8776/architecture
http://localhost:8776/analytics
```

If ports are already in use:

```bash
lsof -i :8776 -i :8775
kill $(lsof -ti :8776 -ti :8775) 2>/dev/null || true
```

## Demo Commands

In the Band room:

```text
@Registry demo
@Reconciler detect
@Reconciler status
@Reconciler resolve-all
@Reconciler resolve <conflict_id> <fact_id> pyproject is executable source of truth
```

This loads a deterministic enterprise config-drift scenario:

- README says Python `3.9`
- `pyproject.toml` requires Python `>=3.11`
- CI uses Python `3.12`
- Dockerfile uses Python `3.10`
- README says install with `pip`
- project metadata says install with `uv`
- architecture docs say Firebase auth
- code says Supabase auth

Manual fallback:

```text
@Keeper reset-demo
@Keeper store subject=runtime predicate=python-version object=3.9 source=README.md
@Keeper store subject=runtime predicate=python-version object=>=3.11 source=pyproject.toml
@Keeper detect
@Reconciler detect
@Reconciler status
```

Bulk resolution uses the safe `ai` strategy by default:

```text
@Reconciler resolve-all
@Reconciler resolve-all ai
```

Only open conflicts with a valid `ai_suggested_fact_id` from their own fact pair are resolved. Missing or invalid suggestions are reported as skipped.

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
- analytics for detected and resolved conflicts over time and by Band message volume

The architecture page presents the complete source-to-resolution flow as a graphical system view and reuses the live mesh counters.

The graphs page reads persistent Band messages and Reconciler decisions to show when conflicts were detected, when they were resolved, and how many messages had been exchanged at each step.

The live timeline is intentionally backed by both Band WebSocket events and local DB polling, so facts, conflicts, and resolutions still appear even if a Band event is missed.

## Why It Fits Band Of Agents

- **At least 3 collaborating agents:** Scraper, Keeper, Reconciler, Registry, and Bridge.
- **Band is central:** agents communicate and hand off work in the Band room.
- **Structured context:** Keeper sends conflict payloads to Reconciler with subject, predicate, sources, and fact IDs.
- **Human-in-the-loop decisions:** Reconciler suggests and records resolutions.
- **Enterprise value:** the workflow catches drift between docs, code, CI, and architecture decisions before teams make decisions from stale information.

## Tests

```bash
.venv/bin/python -m unittest test_unit.py
```

or:

```bash
uv run python -m unittest test_unit.py
```

`test_integration.py` remains the legacy HTTP A2A end-to-end path. The hackathon demo path is the Band-native mesh launched with `scripts/run_mesh.sh`.
