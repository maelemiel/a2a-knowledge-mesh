# Demo Script

## Pitch

A2A Knowledge Mesh detects and resolves enterprise knowledge drift. Documentation, code, support notes, and internal decisions often disagree. Instead of one chatbot answering from stale context, specialized agents collaborate through Band: one extracts facts, one stores and detects contradictions, one reconciles, one tracks capabilities, and one makes the workflow auditable.

## Run

```bash
bash scripts/run_mesh.sh
```

Open the dashboard:

```text
http://localhost:8776
```

## 3-Minute Flow

Reset the demo:

```text
@Keeper reset-demo
```

Create a contradiction:

```text
@Keeper store subject=project-ALLY predicate=framework object=Next.js source=docs
@Keeper store subject=project-ALLY predicate=framework object=FastAPI source=code
```

Detect it:

```text
@Keeper detect
```

Show AI review and handoff:

```text
@Reconciler detect
@Reconciler status
```

Resolve it:

```text
@Reconciler resolve <conflict_id> <winning_fact_id> code is source of truth
```

The dashboard should show:

- facts being stored
- Keeper detecting a conflict
- Reconciler opening/scoring the conflict
- Reconciler resolving it
- the audit history keeping a persistent trace

## Scraper Flow

```text
@Scraper scan self
@Scraper status
```

The Scraper extracts structured facts from the repository and hands them to Keeper in batches. Keeper can then detect contradictions and hand them off to Reconciler.

## Judging Alignment

- **Application of Technology:** Band is the active collaboration layer. Agents mention each other and hand off structured state in-room.
- **Presentation:** Dashboard shows the timeline, audit history, facts, conflicts, resolutions, and registered agents.
- **Business Value:** Solves knowledge drift across code, docs, and enterprise knowledge sources.
- **Originality:** Shows multi-agent review, state coordination, handoff, and persistent auditability rather than a single chatbot.

## Troubleshooting

If the dashboard does not update, restart both ports:

```bash
kill $(lsof -ti :8776 -ti :8775) 2>/dev/null || true
bash scripts/run_mesh.sh
```

Then hard refresh the browser with `Ctrl+Shift+R`.
