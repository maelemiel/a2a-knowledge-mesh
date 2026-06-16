# A2A Knowledge Mesh — Hackathon Submission (lablab.ai)

## Short Description (255 chars max)

3 A2A agents that discover each other, store facts, and actively reconcile contradictions using LLMs — all collaborating inside Band. When docs say X and code says Y, our agents detect it, suggest a winner, and resolve automatically.

## Long Description (100+ words)

A2A Knowledge Mesh is a system of three autonomous Band agents — Registry, Keeper, and Reconciler — that discover each other via A2A Agent Cards and collaborate entirely within Band rooms to keep a team's knowledge consistent.

The problem is universal: documentation says one thing, code says another. Humans rarely notice until it breaks. Our agents solve this by continuously scraping project files (pyproject.toml, README.md, .env.example, documentation) into structured facts stored in SQLite. When facts from different sources contradict each other — same subject, same predicate, different value — the Keeper agent detects the conflict via a SQL JOIN and flags it.

The Reconciler agent then takes over: it calls an LLM (Featherless AI / OpenAI) to analyze both facts, considers source credibility and timestamps, and suggests the correct value. A human can resolve in a Band room with a simple command, or the system auto-resolves based on confidence thresholds. Every conflict, suggestion, and resolution is tracked with full provenance.

What sets us apart from passive sync solutions like Memory Store (YC P26): we don't just mirror data. We actively detect, debate, and resolve contradictions — turning knowledge reconciliation from a manual chore into an autonomous agent workflow.

## Key Differentiators

- **Active reconciliation** (not passive sync): we detect contradictions via SQL JOIN and resolve them via LLM
- **3 agents collaborating in Band**: Registry → Keeper → Reconciler, all in the same Band rooms
- **Interactive Web Dashboard**: served directly by the reconciler agent, featuring a live SVG network topology graph, side-by-side conflict comparisons, and active resolution buttons.
- **Full provenance**: every fact tracks its source, every conflict has an AI suggestion, every resolution is recorded
- **YC RFS alignment**: #5 Company Brain (shared agent knowledge) + #13 Software for Agents
- **Stack**: Python, Band SDK, A2A Protocol, SQLite, Featherless AI, Pydantic

## Project Links

- GitHub: https://github.com/maelemiel/a2a-knowledge-mesh
- Video: [link to 5min demo video]
