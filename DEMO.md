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

Seed the deterministic config-drift demo:

```text
@Registry demo
```

This asks Keeper, through Band, to reset local demo state and load facts that disagree across README, `pyproject.toml`, CI, Dockerfile, docs, and code.

Show the handoff and AI review:

```text
@Reconciler detect
@Reconciler status
```

Resolve every conflict with a valid AI recommendation:

```text
@Reconciler resolve-all
@Reconciler status
```

Or resolve one conflict manually:

```text
@Reconciler resolve <conflict_id> <winning_fact_id> pyproject is executable source of truth
```

Manual fallback if Registry is not registered yet:

```text
@Keeper seed-demo
```

Minimal manual fallback:

```text
@Keeper reset-demo
@Keeper store subject=runtime predicate=python-version object=3.9 source=README.md
@Keeper store subject=runtime predicate=python-version object=>=3.11 source=pyproject.toml
@Keeper detect
@Reconciler detect
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
- **Business Value:** Solves knowledge drift across code, docs, CI, configuration, and enterprise knowledge sources.
- **Originality:** Shows multi-agent review, state coordination, handoff, and persistent auditability rather than a single chatbot.

## 60-Second Version

```text
@Registry demo
@Reconciler detect
@Reconciler status
@Reconciler resolve-all
```

Narration: “Instead of asking one agent for an answer, specialized agents coordinate in Band. Keeper stores evidence, Reconciler reviews contradictions, and the dashboard keeps an audit trail.”

## Troubleshooting

If the dashboard does not update, restart both ports:

```bash
kill $(lsof -ti :8776 -ti :8775) 2>/dev/null || true
bash scripts/run_mesh.sh
```

Then hard refresh the browser with `Ctrl+Shift+R`.
