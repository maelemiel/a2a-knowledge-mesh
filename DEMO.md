# Knowledge Mesh — 3 Minute Judge Demo

## Goal

Show that Band is the coordination layer, not a notification channel.

The agents collaborate in the Band room:

```text
Scraper -> Keeper -> Reconciler -> Human/Dashboard
```

## Before Recording

```bash
uv sync
cp .env.example .env
# fill Band room, 5 agent credentials, FEATHERLESS_API_KEY
bash scripts/run_mesh.sh
```

Open:

```text
http://localhost:8776
```

In Band, reset state:

```text
@Registry reset-demo
```

## Demo Script

### 1. Hook

> "Enterprise knowledge drifts. Code says one thing, docs say another, and humans waste time finding the source of truth. This mesh lets specialized agents coordinate through Band to detect contradictions, review evidence, and track resolution."

### 2. Discovery And Reset

Type:

```text
@Registry list
```

Say:

> "Registry is the directory. Agents self-register in Band with their skills, so the room can discover who can store facts, scrape repositories, or resolve conflicts."

Then:

```text
@Registry reset-demo
```

Dashboard should show zero facts/open conflicts after refresh.

### 3. Full Agent Handoff

Type:

```text
@Scraper slurp git /home/eliott/a2a-knowledge-mesh
```

Say:

> "Scraper parses repository files and sends a structured fact batch to Keeper through Band."

Watch for:

```text
Scraper: Scanning ...
Scraper: Sent N facts to Keeper
Keeper: stored N fact(s)
Keeper: handoff: conflict.detected
Reconciler: conflict(s) detected...
```

Say:

> "Keeper owns the fact store. It uses a SQL JOIN to find contradictions, then mentions Reconciler with structured conflict context. Band is the task handoff layer."

### 4. Reconciler Review

When Reconciler posts:

```text
CONFLIT #...
Fact A ...
Fact B ...
AI suggère ...
Root cause ...
Correctif proposé ...
```

Say:

> "Reconciler scores severity, confidence, source of truth, root cause, and a suggested fix. The human can accept or override."

Manual resolution:

```text
@Reconciler resolve <conflict_id> <fact_id> source_of_truth_confirmed
```

Dashboard should move the pair from:

```text
Conflicts: 1
Resolved: 0
```

to:

```text
Conflicts: 0
Resolved: 1
```

### 5. Fallback Mini-Demo

If repo scraping does not produce a conflict, force one:

```text
@Registry reset-demo
@Keeper store subject=project-ALLY predicate=framework object=Next.js source=docs
@Keeper store subject=project-ALLY predicate=framework object=FastAPI source=code
@Keeper detect
```

Expected:

```text
Keeper -> Reconciler handoff
Reconciler -> AI suggestion
Dashboard -> Facts 2, Conflicts 1
```

## One-Liner

> "Band is the shared operating room: agents exchange structured context, recruit the next specialist, expose the handoff to humans, and keep a traceable resolution trail."
