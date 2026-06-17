# Knowledge Mesh — Judge Demo Script

A tight **3-minute** walkthrough. Every line in quotes is narration to say out
loud; it names the Band primitive doing the work, because that is what the
rubric rewards.

> **Golden rule:** have the **offline path** running and proven *before* you go
> live. If anything breaks, pivot to it without missing a beat — the visuals
> are identical.

---

## 0. One-time setup (before the room)

```bash
uv sync
cp .env.example .env
# fill in: 5 Band agent IDs + keys, Featherless API key, room ID
```

- One Featherless key powers all 4 LLM agents (~$0.05/run)
- Start the dashboard in its own terminal and leave it up:

```bash
uv run python dashboard/server.py
# → http://localhost:8766
```

---

## 1. The hook (20s)

> "Enterprise knowledge is spread across code, documentation, Slack conversations,
> and meeting notes. When two sources say different things — 'version 1.0.0'
> versus 'version 0.9.0' — normally a human spends 30 minutes tracking down
> which one is right. Watch 4 agents do it in under 30 seconds — coordinating
> through Band, catching the contradiction automatically, and resolving it
> without a human lifting a finger."

Open the dashboard. It's the knowledge mesh: live timeline, fact counters,
conflict tracking, all updating in real time.

---

## 2. Live run in Band (90s)

In a second terminal:

```bash
bash scripts/run_mesh.sh
```

Wait for all five "Agent started" lines. Open **app.band.ai** →
room "Knowledge Mesh".

### Step 1 — Scraper ingests facts

The Scraper scans a git repo and posts structured facts into the room:

```
Scraper: 🔍 Scanning project-audit-remediation...
Scraper: ✅ Sent 47 facts to Keeper via store-batch
```

> "The Scraper is a **deterministic agent** — no LLM, just file parsing.
> Band lets it post structured facts into the room that other agents
> consume. **Without Band, this data would be locked inside a script
> that no other agent can discover.**"

### Step 2 — Keeper detects a conflict

Keeper stores the facts in SQLite, then runs the conflict detection query:

```
Keeper: ✅ stored fact #12: project-ally → version = 1.0.0 (from pyproject.toml)
Keeper: ✅ stored fact #13: project-ally → version = 0.9.0 (from README.md)
Keeper: ⚠️ 1 conflict detected:
         project-ally → version: '1.0.0' (src:pyproject.toml) vs '0.9.0' (src:README.md)
Keeper: @reconciler detect — 1 conflict
```

> "Keeper runs a **SQL JOIN** — not an O(n²) memory scan — to find
> contradictory facts from different sources. Then it @mentions the
> Reconciler through Band. **Band routes the problem to the right
> specialist agent, just like an @mention in a Slack channel.**"

### Step 3 — Reconciler resolves with LLM

```
Reconciler: 💡 AI: Fact #12 is correct
            pyproject.toml is the authoritative source for package metadata
            (confidence: 0.95, severity: LOW)
Reconciler: ✅ Auto-resolved → conflict 'abc12345' → fact #12 wins
```

> "The Reconciler calls Featherless AI through our shared provider,
> asks 'which fact is correct', and gets a confident answer. **The
> LLM decision, the confidence score, and the auto-resolution all
> happen inside the Band room — visible to every participant.**"

### Step 4 — Dashboard shows the result

Flip to http://localhost:8766:

- **Messages counter** incremented
- **Timeline** shows every step
- **Conflicts** shows 1 resolved

> "The Bridge agent — a sixth, deterministic agent — mirrors the
> entire conversation to the dashboard via HTTP. **Every @mention,
> every handoff, every resolution is visible in real time. Band
> is not a black box; it's a transparent coordination layer.**"

### (Bonus) Step 5 — Human in the loop

Type in the Band room:

```
@keeper recall project-ally
```

Keeper replies:

```
📋 47 facts:
  #12 version = 1.0.0 (from pyproject.toml)
  #13 version = 0.9.0 (from README.md, OVERRIDDEN by #12)
  #14 dep-python = uvicorn>=0.34
  ...
```

> "The human can query the fact store at any time. **Band keeps
> the human as a first-class participant — not just a spectator,
> but someone who can inspect, challenge, and override agent
> decisions.**"

---

## 3. The dashboard payoff (30s)

Point at http://localhost:8766:

- **Live Timeline** — every Band message in real time
- **Fact count** — how many facts are stored
- **Conflict count** — how many contradictions were found and resolved
- **Bridge status** — shows the Bridge is connected

> "The dashboard polls the Bridge agent every 2.5 seconds. **Without
> Band, you'd need a message bus, a database, and a custom API to
> replicate this — and you'd still lose the agent-to-agent
> coordination.**"

---

## 4. If credits / Wi-Fi / Band fail — the offline path

Everything above runs deterministically with **no API and no Band**:

```bash
uv run python scripts/run_offline_demo.py
```

Then refresh the dashboard. Run the other fixtures to show the system
**discriminates**:

| Fixture | Result |
|---------|--------|
| `fixtures/code_vs_doc.json` | 🔄 Conflict → auto-resolved (LOW) |
| `fixtures/merge_conflict.json` | 🔄 Conflict → needs human (MEDIUM) |
| `fixtures/stale_data.json` | 🔄 Conflict → auto-resolved (MEDIUM) |
| `fixtures/clean_run.json` | ✅ No conflict |

> "Four scenarios, four different outcomes. The same SQLite store,
> the same conflict detection, the same resolution pipeline —
> **Band is the nervous system that connects them, not the brain
> that does the thinking.**"

---

## One-liner if a judge asks "what's Band actually doing?"

> "Band is the coordination layer: structured fact handoff between
> agents, @mention routing to the right specialist, shared context
> without shared memory, and a human-in-the-loop gate at every
> resolution. **Strip Band out and you have 4 SQLite databases
> that can't find or talk to each other.**"
