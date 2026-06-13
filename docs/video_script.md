# Video Script — 5 min max

## [0:00-0:30] Problem

> "Information is everywhere. Nobody knows what's true anymore."

Split screen: GitHub README says "Python/Next.js" / Drive PDF says "Python/FastAPI".

> "Same project, two sources, two truths. Which is correct? You waste time finding out — or worse, you ship the wrong thing."

---

## [0:30-2:30] Demo (record screen, terminal + Band web UI)

**Terminal window:**

```
# Store first fact
$ curl -X POST http://localhost:8766/a2a \
  -d '{"method":"message/send","params":{"message":{"parts":[{"text":"store-fact key=project:ALLY value=Python/Next.js source=github"}]}}}'

"Stored: project:ALLY = Python/Next.js"

# Store conflicting fact (different source)
$ curl -X POST http://localhost:8766/a2a \
  -d '{"method":"message/send","params":{"message":{"parts":[{"text":"store-fact key=project:ALLY value=Python/FastAPI source=drive"}]}}}'

"Stored: project:ALLY = Python/FastAPI"

# Detect conflicts
$ curl -X POST http://localhost:8767/a2a \
  -d '{"method":"message/send","params":{"message":{"parts":[{"text":"detect-conflict"}]}}}'

"Conflict found: project:ALLY -> live says Python/Next.js vs staging says Python/FastAPI"
"Band room created: conflict-project-ALLY"
```

**Switch to Band web UI** — show the room with messages:
- "CONFLICT: project:ALLY has 2 values"
- "RESOLUTION: project:ALLY = Python/Next.js (live is canonical)"

> Voiceover: "The Reconciler detects the contradiction, creates a Band room, posts the conflict, then the resolution. Every agent in the room sees exactly what happened. No more guessing."

---

## [2:30-3:30] Architecture (simple diagram)

Show 3 boxes connected:
- **Registry** → directory of agents (A2A Agent Cards)
- **Keeper** → facts stored in SQLite
- **Reconciler** → detects conflicts → creates Band room

> "3 agents. Each does one thing. They discover each other through A2A Agent Cards — a standard from the Linux Foundation. When a conflict is found, the Reconciler creates a Band room and posts the resolution. All visible, all traceable."

---

## [3:30-4:30] Why It's Different

> "Memory Store syncs your data — but when things disagree, it picks the last write. We negotiate. Our agents don't just store facts, they reconcile them."

**Bullets on screen:**
- Active reconciliation, not passive sync
- Visible in Band rooms, not hidden in a black box
- Standards-based (A2A Protocol, 150+ orgs)

---

## [4:30-5:00] Call to Action

> "A2A Knowledge Mesh — 3 agents that keep your facts straight."
> GitHub: github.com/maelemiel/a2a-knowledge-mesh
> Thanks: AI/ML API, Featherless AI (BOA26), Band
