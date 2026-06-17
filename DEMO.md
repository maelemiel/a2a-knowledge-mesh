# DEMO — A2A Knowledge Mesh

> 5 min walkthrough for jury. Two paths: offline (zero deps) or live (Band).
>
> **Slides:** double-click `Knowledge_Mesh.pptx`
> **Video:** `demo.mp4` in this repo

---

## Path A — Offline Demo (3 min, no Band, no API keys)

Best for dry run / travel / jury desk with no WiFi.

### Step 1 — Setup

```bash
git clone https://github.com/maelemiel/a2a-knowledge-mesh.git
cd a2a-knowledge-mesh
uv sync
```

### Step 2 — Launch quick demo

```bash
uv run python scripts/quick_demo.py
```

This does **everything** in one command:

1. Creates a fresh SQLite database with 8 facts from 2 sources
2. Detects 3 conflicts via SQL JOIN
3. Runs AI resolution (if LLM key found) or creates human-review conflicts
4. Launches dashboard on http://localhost:8766
5. Opens your browser automatically

You see:
- **Facts:** 8 facts ingested from `docs-repo` and `code-repo`
- **Conflicts:** 3 detected (framework, version, database are contradictory)
- **Resolved:** AI-picked winners with explanations
- **Agent list:** 4 registered agents with their skills
- **Live timeline:** All events from the demo

### Step 3 — Walk the jury through

Point at each section:

| Section | What to say |
|---------|-------------|
| **Cards** | "8 facts stored, 3 conflicts detected, 1 auto-resolved — real metrics from SQLite" |
| **Timeline** | "Every action is logged with sender + timestamp — full audit trail" |
| **Registered Agents** | "4 agents discovered each other via the Registry directory" |
| **Cheatsheet** | "Each agent has a command syntax — humans talk to them via @mention in Band" |
| **Reset button** | "One click wipes everything — fresh start for the next demo" |

### Step 4 — Click Reset

Shows the jury that resetting is instant. Then refresh to show the dashboard
gracefully handles empty state.

---

## Path B — Live Demo (5 min, needs Band account + LLM key)

### Setup

```bash
cp .env.example .env
# Fill in: BAND_*_ID, BAND_*_KEY, BAND_ROOM_ID, BAND_USER_HANDLE
# At least one LLM key (Featherless / OpenAI / AIML)
bash scripts/run_mesh.sh
```

Open dashboard → http://localhost:8766
Open Band → your room with all 5 agents

### Demo Script

**1. List agents** — show that Registry works

```
@registry list
```

> Expected: agents appear in dashboard Registered Agents panel

**2. Scan the repo with AI** — Scraper uses LLM to extract real facts from code

```
@scraper scan self
```

> This is the showpiece: the Scraper agent reads every file in the repo
> (`pyproject.toml`, Python source, docs, configs) and asks the LLM to
> extract structured facts: "what language, framework, database, tools
> does this project use?"
>
> **Expected output:**
> ```
> 🔍 Scanning a2a-knowledge-mesh with LLM (Featherless)...
> 📊 Scan complete: 12 facts from 6 files (4 code, 2 doc)
> ✅ Sent 12 facts to @keeper (1 messages)
> ```
>
> The dashboard now shows ~12 facts from `scraper` source.
>
> **Pro tip:** The LLM extracts facts in real time — point at the
> dashboard counter jumping from 0 to 12 as proof of live AI work.

**3. Add contradictory "documentation" facts** — simulate stale docs

Now we add facts that contradict what the code actually says.
This simulates a real-world scenario: docs are outdated vs code is current.

```
@keeper store subject=a2a-knowledge-mesh predicate=framework object=FastAPI source=docs-repo
@keeper store subject=a2a-knowledge-mesh predicate=database object=MySQL source=docs-repo
@keeper store subject=a2a-knowledge-mesh predicate=language object=JavaScript source=docs-repo
```

> Expected: 3 facts stored. Keeper auto-detects conflicts with the
> Scraper's facts (Starlette vs FastAPI, SQLite vs MySQL, Python vs JS).

**4. Detect conflicts**

```
@keeper detect
```

or

```
@reconciler detect
```

> Expected: 3 conflicts found. Each shows:
> ```
> ⚠️ Conflict on a2a-knowledge-mesh / framework:
>   Fact A: Starlette (scraper — from pyproject.toml)
>   Fact B: FastAPI (docs-repo — stale documentation)
> ```

**5. Check status** — see AI recommendations

```
@reconciler status
```

> Expected: shows each conflict with AI confidence scores.
> The LLM should favor the Scraper's code-extracted facts over
> the manual docs-repo entries — because code doesn't lie.

**6. Resolve** — accept AI suggestion

```
@reconciler resolve <conflict_id> <suggested_fact_id> Code is source of truth — docs are outdated
```

> Replace `<conflict_id>` and `<suggested_fact_id>` with actual values
> from `status`. Expected: conflict moves to "resolved", dashboard
> counters update, timeline shows resolution event.

**7. Dashboard walkthrough** — same as Path A Step 3

### Why this flow wins jury points

| What they see | Why it matters |
|---------------|----------------|
| **LLM extracts facts from real code** | Not a scripted demo — the AI actually reads pyproject.toml and generates facts live |
| **Live conflict with stale docs** | Real-world problem every company has |
| **AI picks code over docs** | Shows the mesh understands provenance and trust |
| **Everything visible in dashboard** | Full audit trail, no black box |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Agents not responding in Band | Check all 5 agents are added to the same room |
| `cannot_mention_self` | Registry auto-registration disabled — normal |
| Dashboard "Bridge offline" | Bridge agent not connected to Band yet (wait 5s) |
| LLM resolution not running | No API key set — falls back to human-review mode |
| Agents flood help text | You typed a bare @mention — fixed by empty-message guard |

---

## Pitch

> "5 Band agents. Each does one thing: discover, store, reconcile, scan, bridge.
> They find each other, coordinate through Band, and negotiate truth together.
> When two sources disagree, SQL JOIN detects it, an LLM suggests a winner,
> and a human decides in the chat room. Full audit trail, zero external infra."

**YC RFS:** #5 Company Brain + #13 Software for Agents
