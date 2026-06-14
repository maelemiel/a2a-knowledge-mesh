# Submission — A2A Knowledge Mesh

## Title (max 80 chars)

> A2A Knowledge Mesh — 3 agents that discover, store, and reconcile facts

## Short Description (max 255 chars)

> 3 A2A agents (Registry, Keeper, Reconciler) that discover each other via Agent Cards, store structured facts in SQLite, and detect contradictions across sources — then create a Band room to post the conflict and its resolution.

## Long Description (100+ words)

Companies lose time and trust when the same information lives in different places with different values. One source says "project ALLY uses Python/Next.js", another says "FastAPI". Which is correct?

A2A Knowledge Mesh solves this with 3 specialized agents communicating through the A2A Agent-to-Agent Protocol (Linux Foundation standard, 150+ orgs) and coordinating via Band.

The **Registry Agent** maintains a directory — every agent publishes its capabilities via an A2A Agent Card (`/.well-known/agent-card.json`). The **Keeper Agent** stores and retrieves structured facts in SQLite with source tracking. The **Reconciler Agent** compares facts across stores, detects contradictions using SQL joins, creates a Band room, posts the conflict with @mentions, and records the resolution.

This turns fragmented knowledge into a living, negotiated truth. The demo shows a client storing a fact, another source contradicting it, the Reconciler detecting the conflict, creating a Band room, and posting the resolution — all in real-time.

Built with Python, A2A Protocol, Band REST API, and SQLite. Uses AI/ML API credits and Featherless AI (BOA26) for LLM-powered resolution suggestions.

Track: Internal Enterprise Workflows / Regulated & High-Stakes.

## Demo Video Script (60s)

```
[0-10s] Terminal: Run `python test_integration.py` — 3 agents start
[10-20s] Registry Agent: Register keeper, discover by skill
[20-35s] Keeper Agent: Store 2 conflicting facts from different sources
[35-50s] Reconciler Agent: Detect conflict, create Band room, post details
[50-60s] Resolve: Pick winning fact, show conflict closed
```

## Setup (for judges)

```bash
git clone https://github.com/maelemiel/a2a-knowledge-mesh.git
cd a2a-knowledge-mesh
uv sync
uv run agents/runner.py          # start all 3 agents
# in another terminal:
uv run test_integration.py        # run the demo flow
```
