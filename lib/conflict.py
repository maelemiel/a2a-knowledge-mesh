"""Conflict detection logic. Compare two fact stores for contradictions."""

import sqlite3
from pathlib import Path


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
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
    conn.commit()
    return conn


def upsert(conn: sqlite3.Connection, store: str, key: str, value: str, source: str = "manual"):
    conn.execute("""INSERT INTO facts (store, key, value, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(store, key) DO UPDATE
        SET value=excluded.value, source=excluded.source, updated_at=CURRENT_TIMESTAMP""",
        (store, key, value, source))
    conn.commit()


def get_facts(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute("SELECT store, key, value, source FROM facts ORDER BY store, key").fetchall()


def detect_conflicts(conn: sqlite3.Connection) -> list[dict]:
    """Find same key with different values across stores.
    Returns list of {key, store_a, value_a, store_b, value_b}."""
    rows = conn.execute("""
        SELECT f1.key, f1.store, f1.value, f2.store, f2.value
        FROM facts f1
        JOIN facts f2 ON f1.key = f2.key AND f1.store < f2.store AND f1.value != f2.value
    """).fetchall()
    return [
        {"key": r[0], "store_a": r[1], "value_a": r[2], "store_b": r[3], "value_b": r[4]}
        for r in rows
    ]


def resolve_conflict(conn: sqlite3.Connection, key: str, winning_store: str):
    """Resolve a conflict by keeping the winning store's value and updating all others."""
    row = conn.execute(
        "SELECT value, source FROM facts WHERE store = ? AND key = ?",
        (winning_store, key)
    ).fetchone()
    if not row:
        raise ValueError(f"No fact for {key} in store {winning_store}")
    value, source = row
    conn.execute("UPDATE facts SET value = ?, source = ? WHERE key = ?",
                 (value, f"resolved:{source}", key))
    conn.commit()
