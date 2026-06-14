"""Keeper Agent — structured fact store with source tracking."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from agents.base import Agent
from protocols.a2a import AgentCard


DB_PATH = Path(__file__).parent.parent / "data" / "keeper.db"


class KeeperStore:
    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_url TEXT,
                timestamp INTEGER NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id)")
        self.conn.commit()

    def store(self, subject: str, predicate: str, object: str, source_id: str, source_url: str | None = None) -> dict:
        ts = int(time.time())
        cur = self.conn.execute(
            "INSERT INTO facts (subject, predicate, object, source_id, source_url, timestamp, version) "
            "VALUES (?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT MAX(version) + 1 FROM facts WHERE subject=? AND predicate=? AND source_id=?), 1))",
            (subject, predicate, object, source_id, source_url, ts, subject, predicate, source_id),
        )
        self.conn.commit()
        return {"id": cur.lastrowid, "subject": subject, "predicate": predicate, "object": object}

    def recall(self, subject: str | None = None, source_id: str | None = None) -> list[dict]:
        query = "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version FROM facts WHERE 1=1"
        params: list[Any] = []
        if subject:
            query += " AND subject = ?"
            params.append(subject)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " ORDER BY timestamp DESC"
        rows = self.conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0], "subject": r[1], "predicate": r[2], "object": r[3],
                "source_id": r[4], "source_url": r[5], "timestamp": r[6], "version": r[7],
            }
            for r in rows
        ]

    def list_all(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version "
            "FROM facts ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            {
                "id": r[0], "subject": r[1], "predicate": r[2], "object": r[3],
                "source_id": r[4], "source_url": r[5], "timestamp": r[6], "version": r[7],
            }
            for r in rows
        ]


class KeeperAgent(Agent):
    card = AgentCard(
        name="Keeper Agent",
        description="Structured knowledge store with source tracking",
        url="http://localhost:8766",
        skills=["store-fact", "recall", "list-facts"],
    )
    port = 8766

    def __init__(self) -> None:
        super().__init__()
        self.store = KeeperStore()

    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        if method == "store-fact":
            return self.store.store(
                subject=params["subject"],
                predicate=params["predicate"],
                object=params["object"],
                source_id=params.get("source_id", "default"),
                source_url=params.get("source_url"),
            )

        if method == "recall":
            return {"facts": self.store.recall(
                subject=params.get("subject"),
                source_id=params.get("source_id"),
            )}

        if method == "list-facts":
            return {"facts": self.store.list_all(
                limit=params.get("limit", 50),
                offset=params.get("offset", 0),
            )}

        raise ValueError(f"unknown method: {method}")


if __name__ == "__main__":
    KeeperAgent().run()
