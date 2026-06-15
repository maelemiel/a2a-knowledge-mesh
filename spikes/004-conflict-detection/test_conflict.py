"""Spike 004: Conflict Detection.
Tests if Reconciler can detect contradictions between two fact stores."""

import sqlite3
import os

FACTS_DB = "/tmp/spike004_facts.db"

def reset_db():
    if os.path.exists(FACTS_DB):
        os.remove(FACTS_DB)

def create_store(store_name):
    conn = sqlite3.connect(FACTS_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        source TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_store_key
        ON facts(store, key)""")
    return conn

def upsert(conn, store, key, value, source="manual"):
    conn.execute("""INSERT INTO facts (store, key, value, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(store, key) DO UPDATE SET value=excluded.value, source=excluded.source""",
        (store, key, value, source))
    conn.commit()

def get_all(conn):
    return conn.execute("SELECT store, key, value, source FROM facts ORDER BY store, key").fetchall()

def detect_conflicts(conn):
    """Find same key with different values across stores."""
    rows = conn.execute("""
        SELECT f1.key, f1.store, f1.value, f2.store, f2.value
        FROM facts f1
        JOIN facts f2 ON f1.key = f2.key AND f1.store < f2.store AND f1.value != f2.value
    """).fetchall()
    return rows

reset_db()
conn = create_store("test")

# Set up: same key, different values in different stores
upsert(conn, "live", "project:ALLY", "Python/Next.js", "agent-1")
upsert(conn, "staging", "project:ALLY", "Python/FastAPI", "agent-2")
upsert(conn, "live", "project:hackathon", "A2A", "agent-1")
upsert(conn, "staging", "project:hackathon", "A2A", "agent-2")  # same value → no conflict

print("=== All Facts ===")
for r in get_all(conn):
    print(f"  [{r[0]}] {r[1]} = {r[2]} (from {r[3]})")

conflicts = detect_conflicts(conn)
print(f"\n=== Conflicts Detected: {len(conflicts)} ===")
for key, s1, v1, s2, v2 in conflicts:
    print(f"  '{key}': [{s1}] says '{v1}' vs [{s2}] says '{v2}' ❌")

# Test fact with same value → no conflict
assert len(conflicts) == 1, f"Expected 1 conflict, got {len(conflicts)}"
assert conflicts[0][0] == "project:ALLY"
print(f"\n✅ VALIDATED: Conflict correctly detected for key '{conflicts[0][0]}'")

conn.close()
os.remove(FACTS_DB)
