#!/usr/bin/env python3
"""quick_demo.py — Try A2A Knowledge Mesh in 30 seconds, zero Band needed.

Creates fixture data in SQLite, detects conflicts, then launches
a standalone dashboard on http://localhost:8766.

Usage:
    uv run python scripts/quick_demo.py         # create fixtures + serve
    uv run python scripts/quick_demo.py --serve  # same, explicit flag
    uv run python scripts/quick_demo.py --clean  # delete + recreate
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"

# ── Fixture data ────────────────────────────────────────────────────────

FIXTURE_AGENTS = [
    {
        "id": "agent-keeper-001",
        "name": "Keeper",
        "skills": json.dumps(["store", "recall", "list", "detect"]),
        "description": "SQLite fact store — knowledge memory of the mesh",
        "url": "",
        "role": "agent",
    },
    {
        "id": "agent-reconciler-001",
        "name": "Reconciler",
        "skills": json.dumps(["detect", "status", "resolve"]),
        "description": "Conflict detection and AI-powered resolution",
        "url": "",
        "role": "agent",
    },
    {
        "id": "agent-scraper-001",
        "name": "Scraper",
        "skills": json.dumps(["scan"]),
        "description": "LLM-based repo scanner — extracts facts from code",
        "url": "",
        "role": "agent",
    },
    {
        "id": "agent-registry-001",
        "name": "Registry",
        "skills": json.dumps(["register", "discover", "list"]),
        "description": "Agent directory — discover agents by skill",
        "url": "",
        "role": "agent",
    },
]

# 8 facts from 2 sources. Some conflict, one matches.
# Language matches (TypeScript both) — no conflict.
# Framework (Next.js vs React), version (18.2 vs 16.8), database (PostgreSQL vs MongoDB) — conflicts.
FIXTURE_FACTS = [
    # source: docs-repo (official documentation)
    (1, "project-ALLY", "framework", "Next.js", "docs-repo", "", "doc", int(time.time()) - 86400),
    (2, "project-ALLY", "version", "18.2", "docs-repo", "", "doc", int(time.time()) - 86400),
    (3, "project-ALLY", "database", "PostgreSQL", "docs-repo", "", "doc", int(time.time()) - 86400),
    (4, "project-ALLY", "language", "TypeScript", "docs-repo", "", "doc", int(time.time()) - 86400),
    # source: code-repo (actual code analysis)
    (5, "project-ALLY", "framework", "React", "code-repo", "", "code", int(time.time()) - 43200),
    (6, "project-ALLY", "version", "16.8", "code-repo", "", "code", int(time.time()) - 43200),
    (7, "project-ALLY", "database", "MongoDB", "code-repo", "", "code", int(time.time()) - 43200),
    (8, "project-ALLY", "language", "TypeScript", "code-repo", "", "code", int(time.time()) - 43200),
]

# 3 conflicts: framework, version, database
FIXTURE_CONFLICTS = [
    {
        "id": "conflict-framework-001",
        "subject": "project-ALLY",
        "predicate": "framework",
        "fact_a_id": 1,
        "fact_b_id": 5,
        "source_a": "docs-repo",
        "source_b": "code-repo",
        "status": "open",
        "created_at": int(time.time()) - 21600,
        "ai_suggested_fact_id": 1,
        "ai_reason": "Next.js is built on top of React, making it the more precise framework descriptor. The 'docs-repo' source is the official documentation and should be authoritative for architectural decisions.",
        "severity": "medium",
        "score_confidence": 0.87,
    },
    {
        "id": "conflict-version-002",
        "subject": "project-ALLY",
        "predicate": "version",
        "fact_a_id": 2,
        "fact_b_id": 6,
        "source_a": "docs-repo",
        "source_b": "code-repo",
        "status": "resolved",
        "created_at": int(time.time()) - 21600,
        "resolved_at": int(time.time()) - 10800,
        "resolution_fact_id": 2,
        "resolution_reason": "docs-repo is the canonical source for version requirements. Code-repo may be running on a local override.",
        "ai_suggested_fact_id": 2,
        "ai_reason": "Documentation specifies 18.2 which aligns with the latest stable Next.js release. Code-repo running 16.8 suggests a local override that hasn't been updated.",
        "severity": "high",
        "score_confidence": 0.92,
    },
    {
        "id": "conflict-database-003",
        "subject": "project-ALLY",
        "predicate": "database",
        "fact_a_id": 3,
        "fact_b_id": 7,
        "source_a": "docs-repo",
        "source_b": "code-repo",
        "status": "open",
        "created_at": int(time.time()) - 21600,
        "ai_suggested_fact_id": 7,
        "ai_reason": "The code-repo shows actual import/connection statements for MongoDB. Documentation may be outdated or describing a planned migration that hasn't happened yet.",
        "severity": "critical",
        "score_confidence": 0.75,
    },
]

# Synthetic timeline events for the dashboard
FIXTURE_EVENTS = [
    {
        "type": "system",
        "sender_name": "Registry",
        "content": "✅ Agent registered: Keeper (skills: store, recall, list, detect)",
        "timestamp": (time.time() - 86400) * 1000,
    },
    {
        "type": "system",
        "sender_name": "Registry",
        "content": "✅ Agent registered: Reconciler (skills: detect, status, resolve)",
        "timestamp": (time.time() - 86400 + 1) * 1000,
    },
    {
        "type": "system",
        "sender_name": "Registry",
        "content": "✅ Agent registered: Scraper (skills: scan)",
        "timestamp": (time.time() - 86400 + 2) * 1000,
    },
    {
        "type": "system",
        "sender_name": "Registry",
        "content": "✅ Agent registered: Registry (skills: register, discover, list)",
        "timestamp": (time.time() - 86400 + 3) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Scraper",
        "content": "📥 Stored fact: project-ALLY framework=React (source: code-repo)",
        "timestamp": (time.time() - 43200) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Scraper",
        "content": "📥 Stored fact: project-ALLY database=MongoDB (source: code-repo)",
        "timestamp": (time.time() - 43200 + 1) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Keeper",
        "content": "✅ Stored 8 facts from 2 sources (docs-repo, code-repo)",
        "timestamp": (time.time() - 21600) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Keeper",
        "content": "⚠️ Detected 3 conflicts on project-ALLY (framework, version, database)",
        "timestamp": (time.time() - 21500) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Reconciler",
        "content": "🤖 AI analysis complete — 3 conflicts scored. Suggested winners with confidence ratings.",
        "timestamp": (time.time() - 21000) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Reconciler",
        "content": "✅ Resolved: project-ALLY version → docs-repo is winner (confidence: 0.92)",
        "timestamp": (time.time() - 10800) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Human",
        "content": "@reconciler status — show me what's open",
        "timestamp": (time.time() - 3600) * 1000,
    },
    {
        "type": "message",
        "sender_name": "Reconciler",
        "content": "📊 2 conflicts open (framework, database), 1 resolved (version)",
        "timestamp": (time.time() - 3595) * 1000,
    },
]


# ── Fixtures builder ────────────────────────────────────────────────────


def _create_db(db_name: str, schema_sql: str) -> Path:
    """Create/recreate a SQLite database with the given schema."""
    import sqlite3

    db_path = DATA / db_name
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        # Clear existing data but keep file
        conn = sqlite3.connect(str(db_path))
        conn.executescript(schema_sql)
        conn.commit()
        conn.close()
    else:
        conn = sqlite3.connect(str(db_path))
        conn.executescript(schema_sql)
        conn.commit()
        conn.close()

    return db_path


def _load_fixtures() -> None:
    """Populate all three databases with fixture data."""
    import sqlite3

    DATA.mkdir(parents=True, exist_ok=True)

    # ── Registry DB ────────────────────────────────────────────────
    reg_schema = """
        DROP TABLE IF EXISTS agents;
        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            card_url TEXT,
            skills TEXT NOT NULL,
            url TEXT,
            last_seen INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'agent',
            description TEXT
        );
    """
    _create_db("registry.db", reg_schema)
    conn = sqlite3.connect(str(DATA / "registry.db"))
    now = int(time.time())
    for a in FIXTURE_AGENTS:
        conn.execute(
            "INSERT INTO agents (id, name, skills, url, last_seen, role, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a["id"], a["name"], a["skills"], a["url"], now, a["role"], a["description"]),
        )
    conn.commit()
    conn.close()
    print("  ✓ Registry: 4 agents registered")

    # ── Keeper DB ──────────────────────────────────────────────────
    keeper_schema = """
        DROP TABLE IF EXISTS facts;
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_url TEXT,
            source_type TEXT DEFAULT 'code',
            timestamp INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX idx_facts_subject ON facts(subject);
        CREATE INDEX idx_facts_source ON facts(source_id);
        CREATE INDEX idx_facts_spo ON facts(subject, predicate, object);
        CREATE INDEX idx_facts_sp ON facts(subject, predicate);
    """
    _create_db("keeper.db", keeper_schema)
    conn = sqlite3.connect(str(DATA / "keeper.db"))
    for f in FIXTURE_FACTS:
        conn.execute(
            "INSERT INTO facts (subject, predicate, object, source_id, source_url, source_type, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f[1], f[2], f[3], f[4], f[5], f[6], f[7]),
        )
    conn.commit()
    conn.close()
    print("  ✓ Keeper: 8 facts stored (4 from docs-repo, 4 from code-repo)")

    # ── Reconciler DB ──────────────────────────────────────────────
    rec_schema = """
        DROP TABLE IF EXISTS conflicts;
        CREATE TABLE conflicts (
            id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            fact_a_id INTEGER NOT NULL,
            fact_b_id INTEGER NOT NULL,
            source_a TEXT NOT NULL,
            source_b TEXT NOT NULL,
            band_room_id TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            resolution_fact_id INTEGER,
            resolution_reason TEXT,
            created_at INTEGER NOT NULL,
            resolved_at INTEGER,
            ai_suggested_fact_id INTEGER,
            ai_reason TEXT,
            semantic_confidence REAL,
            semantic_reason TEXT,
            severity TEXT,
            score_confidence REAL,
            auto_resolved INTEGER DEFAULT 0,
            root_cause TEXT,
            truth_source TEXT,
            suggested_fix TEXT,
            fix_file TEXT
        );
    """
    _create_db("reconciler.db", rec_schema)
    conn = sqlite3.connect(str(DATA / "reconciler.db"))
    for c in FIXTURE_CONFLICTS:
        conn.execute(
            "INSERT INTO conflicts (id, subject, predicate, fact_a_id, fact_b_id, "
            "source_a, source_b, status, created_at, resolved_at, "
            "resolution_fact_id, resolution_reason, "
            "ai_suggested_fact_id, ai_reason, severity, score_confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c["id"],
                c["subject"],
                c["predicate"],
                c["fact_a_id"],
                c["fact_b_id"],
                c["source_a"],
                c["source_b"],
                c["status"],
                c["created_at"],
                c.get("resolved_at"),
                c.get("resolution_fact_id"),
                c.get("resolution_reason"),
                c.get("ai_suggested_fact_id"),
                c.get("ai_reason"),
                c.get("severity"),
                c.get("score_confidence"),
            ),
        )
    conn.commit()
    conn.close()
    print("  ✓ Reconciler: 3 conflicts (2 open, 1 resolved)")


# ── Server patch: inject fixture events ─────────────────────────────────


def _inject_fixture_events():
    """Monkey-patch the dashboard server to serve fixture events when bridge is down."""
    import dashboard.server as srv

    original_fetch = srv._fetch

    def _patched_fetch(url: str) -> dict:
        result = original_fetch(url)
        if "error" in result:
            # Bridge down — return fixture events
            return {"events": FIXTURE_EVENTS, "error": None}
        return result

    srv._fetch = _patched_fetch


# ── Main ────────────────────────────────────────────────────────────────


def main():
    import webbrowser

    # --clean not implemented yet — always recreates fixtures
    if "--clean" in sys.argv:
        pass  # will recreate

    print(textwrap.dedent("""\
    ╔══════════════════════════════════════════════╗
    ║     A2A Knowledge Mesh — Quick Demo         ║
    ║  8 facts · 3 conflicts · 1 command          ║
    ╚══════════════════════════════════════════════╝
    """))

    # Wipe and recreate fixtures
    _load_fixtures()
    print()

    # Launch dashboard
    _inject_fixture_events()
    from dashboard.server import main as serve

    port = int(os.getenv("DASHBOARD_PORT", "8766"))
    print(f"  🌐 Dashboard → http://localhost:{port}\n")

    # Open browser
    webbrowser.open(f"http://localhost:{port}")

    print("  Press Ctrl+C to stop.\n")
    serve()


if __name__ == "__main__":
    main()
