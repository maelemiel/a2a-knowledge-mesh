"""Keeper Agent — structured fact store with source tracking and
server-side conflict detection.

New RPC method ``detect-conflicts`` replaces the old Reconciler-side
O(n²) in-memory scan with a single SQL JOIN.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from agents.base import Agent
from agents.validation import ListFactsParams, RecallParams, StoreFactParams
from protocols.a2a import AgentCard

logger = logging.getLogger(__name__)


DB_PATH = Path(__file__).parent.parent / "data" / "keeper.db"


class KeeperStore:
    """SQLite-backed fact store with WAL mode and conflict-detection queries."""

    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")  # 64 MB cache
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
        # Indexes for fast lookups and conflict detection
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_spo ON facts(subject, predicate, object)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_sp ON facts(subject, predicate)")
        self.conn.commit()
        logger.info("KeeperStore ready at %s", db_path)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store(
        self,
        subject: str,
        predicate: str,
        object: str,
        source_id: str = "band",
        source_url: str | None = None,
    ) -> dict:
        ts = int(time.time())
        cur = self.conn.execute(
            "INSERT INTO facts (subject, predicate, object, source_id, source_url, timestamp, version) "
            "VALUES (?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT MAX(version) + 1 FROM facts WHERE subject=? AND predicate=? AND source_id=?), 1))",
            (subject, predicate, object, source_id, source_url, ts, subject, predicate, source_id),
        )
        self.conn.commit()
        return {
            "id": cur.lastrowid,
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "source_id": source_id,
        }

    def store_batch(self, facts: list[dict]) -> list[dict]:
        """Bulk insert multiple facts in a single transaction."""
        ts = int(time.time())
        ids: list[dict] = []
        for f in facts:
            source = f.get("source_id", "band")
            cur = self.conn.execute(
                "INSERT INTO facts (subject, predicate, object, source_id, source_url, timestamp, version) "
                "VALUES (?, ?, ?, ?, ?, ?, "
                "COALESCE((SELECT MAX(version) + 1 FROM facts WHERE subject=? AND predicate=? AND source_id=?), 1))",
                (
                    f["subject"],
                    f["predicate"],
                    f["object"],
                    source,
                    f.get("source_url"),
                    ts,
                    f["subject"],
                    f["predicate"],
                    source,
                ),
            )
            ids.append(
                {
                    "id": cur.lastrowid,
                    "subject": f["subject"],
                    "predicate": f["predicate"],
                    "object": f["object"],
                    "source_id": source,
                }
            )
        self.conn.commit()
        logger.info("Stored %d facts in batch", len(ids))
        return ids

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def recall(
        self,
        subject: str | None = None,
        source_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        query = "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version FROM facts WHERE 1=1"
        params: list[Any] = []
        if subject:
            query += " AND subject = ?"
            params.append(subject)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " ORDER BY timestamp DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "subject": r[1],
                "predicate": r[2],
                "object": r[3],
                "source_id": r[4],
                "source_url": r[5],
                "timestamp": r[6],
                "version": r[7],
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
                "id": r[0],
                "subject": r[1],
                "predicate": r[2],
                "object": r[3],
                "source_id": r[4],
                "source_url": r[5],
                "timestamp": r[6],
                "version": r[7],
            }
            for r in rows
        ]

    def get_by_id(self, fact_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version "
            "FROM facts WHERE id=?",
            (fact_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "subject": row[1],
            "predicate": row[2],
            "object": row[3],
            "source_id": row[4],
            "source_url": row[5],
            "timestamp": row[6],
            "version": row[7],
        }

    def get_fact(self, fact_id: int) -> dict | None:
        return self.get_by_id(fact_id)

    # ------------------------------------------------------------------
    # Conflict detection — SQL JOIN (O(n log n), not O(n²))
    # ------------------------------------------------------------------

    def detect_conflicts(self, limit: int = 200, offset: int = 0) -> list[dict]:
        """Find (subject, predicate) pairs with conflicting objects from different sources.

        Returns a list of conflict pairs::

            {"subject", "predicate", "fact_a_id", "fact_b_id",
             "source_a", "source_b", "object_a", "object_b",
             "timestamp_a", "timestamp_b"}
        """
        rows = self.conn.execute(
            """
            SELECT f1.subject, f1.predicate,
                   f1.id AS fact_a_id, f2.id AS fact_b_id,
                   f1.source_id AS source_a, f2.source_id AS source_b,
                   f1.object AS object_a, f2.object AS object_b,
                   f1.timestamp AS timestamp_a, f2.timestamp AS timestamp_b
            FROM facts f1
            JOIN facts f2
              ON f1.subject = f2.subject
             AND f1.predicate = f2.predicate
             AND f1.source_id < f2.source_id        -- avoid duplicate pairs (a,b) vs (b,a)
             AND f1.object != f2.object               -- only real contradictions
            ORDER BY f1.subject, f1.predicate
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [
            {
                "subject": r[0],
                "predicate": r[1],
                "fact_a_id": r[2],
                "fact_b_id": r[3],
                "source_a": r[4],
                "source_b": r[5],
                "object_a": r[6],
                "object_b": r[7],
                "timestamp_a": r[8],
                "timestamp_b": r[9],
            }
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()

    def clear(self) -> int:
        """Delete all facts. Returns count of deleted rows."""
        count = self.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        self.conn.execute("DELETE FROM facts")
        self.conn.commit()
        return count


class KeeperAgent(Agent):
    card = AgentCard(
        name="Keeper Agent",
        description="Structured knowledge store with source tracking",
        url="http://localhost:8766",
        skills=["store-fact", "store-facts-batch", "recall", "list-facts", "detect-conflicts"],
        authentication={"schemes": [{"type": "bearer"}]},
    )
    port = 8766
    agent_role = "keeper"

    def __init__(self) -> None:
        super().__init__()
        self.store = KeeperStore()
        self.connection = self.store.conn

    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        if method == "store-fact":
            p = StoreFactParams(**params)
            return self.store.store(
                subject=p.subject,
                predicate=p.predicate,
                object=p.object,
                source_id=p.source_id,
                source_url=p.source_url,
            )

        if method == "store-facts-batch":
            from agents.validation import StoreFactsBatchParams

            p = StoreFactsBatchParams(**params)
            return {"facts": self.store.store_batch([f.model_dump() for f in p.facts])}

        if method == "recall":
            p = RecallParams(**params)
            return {"facts": self.store.recall(subject=p.subject, source_id=p.source_id)}

        if method == "list-facts":
            p = ListFactsParams(**params)
            return {"facts": self.store.list_all(limit=p.limit, offset=p.offset)}

        if method == "detect-conflicts":
            limit = params.get("limit", 200)
            offset = params.get("offset", 0)
            return {"conflicts": self.store.detect_conflicts(limit=limit, offset=offset)}

        if method == "get-fact":
            return {"fact": self.store.get_by_id(int(params["id"]))}

        raise ValueError(f"unknown method: {method}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    KeeperAgent().run()
