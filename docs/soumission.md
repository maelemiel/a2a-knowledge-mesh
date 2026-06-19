# A2A Knowledge Mesh — Hackathon Submission (lablab.ai)

## Project Title

A2A Knowledge Mesh

## Short Description (255 chars max)

Five Band agents that detect and resolve enterprise knowledge drift. When docs say
Python 3.9 and pyproject.toml says >=3.11, our agents catch it, score it with an
LLM, and resolve it — automatically or with one human command.

(252 chars)

## Long Description (100+ words)

A2A Knowledge Mesh coordinates five specialized Band agents in one shared room
to combat knowledge drift — the silent disconnect when docs, code, CI configs,
and deployment specs fall out of sync.

The workflow: Scraper reads a repo with an LLM and extracts structured facts
(subject, predicate, object + source + timestamp). Keeper stores them in SQLite
and detects contradictions with a single JOIN query (O(n log n) — no memory
scan). Reconciler takes each conflict, runs it through an LLM (Featherless /
OpenAI), and scores severity, confidence, and a suggested winner. Registry
provides agent discovery. Bridge mirrors everything to a frosted-glass web
dashboard with live timeline, metrics, and audit history.

Resolution is human-in-the-loop: one `@reconciler resolve` command in the Band
room, or fully automatic when LLM confidence exceeds a threshold. Every fact,
conflict, and resolution is tracked with full provenance (source, timestamp,
version).

The `mesh graphify` command renders the knowledge mesh as an interactive Vis.js
graph — blue subjects, green valid facts, red conflicts. Click any node to
inspect versions, timestamps, and source files.

Unlike passive sync solutions (Memory Store, YC P26), we don't just copy data.
We actively detect, score, and resolve contradictions. Stack: Python, Band SDK,
SQLite, Featherless AI, Starlette, Vis.js.

## Technology & Category Tags

- Band of Agents
- Multi-Agent Systems
- Knowledge Management
- AI / LLM
- Developer Tools
- Python
- SQLite
- Agent-to-Agent (A2A)
- Enterprise Software

## Project Links

- GitHub: https://github.com/maelemiel/a2a-knowledge-mesh
- Cover Image: [docs/cover-image-prompt.md](docs/cover-image-prompt.md)
- Slide Deck: A2A_Knowledge_Mesh_Deck.pdf
- Video Script: [docs/video-script.md](docs/video-script.md)
- Agent Documentation: [docs/agents.md](docs/agents.md)
