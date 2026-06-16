"""Keeper Agent — Band-native fact store.

Listens in Band rooms for commands like:

  @keeper store subject=X predicate=Y object=Z source=docs
  @keeper recall project-ALLY
  @keeper list

Stores facts in SQLite. Replies in the room.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "keeper.db"


class KeeperStore:
    """SQLite-backed fact store."""

    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT 'band',
                source_url TEXT,
                timestamp INTEGER NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_lookup
            ON facts(subject, predicate)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_source
            ON facts(source_id)
        """)
        self.conn.commit()

    def store(self, subject: str, predicate: str, object: str,
              source_id: str = "band", source_url: str | None = None) -> dict:
        ts = int(time.time())
        cur = self.conn.execute(
            "INSERT INTO facts (subject, predicate, object, source_id, source_url, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (subject, predicate, object, source_id, source_url, ts),
        )
        self.conn.commit()
        return {"id": cur.lastrowid, "subject": subject, "predicate": predicate,
                "object": object, "source_id": source_id}

    def recall(self, subject: str | None = None) -> list[dict]:
        if subject:
            rows = self.conn.execute(
                "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version "
                "FROM facts WHERE subject=? ORDER BY timestamp DESC",
                (subject,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version "
                "FROM facts ORDER BY timestamp DESC LIMIT 50"
            ).fetchall()
        return [
            {"id": r[0], "subject": r[1], "predicate": r[2], "object": r[3],
             "source_id": r[4], "source_url": r[5], "timestamp": r[6], "version": r[7]}
            for r in rows
        ]

    def list_all(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version "
            "FROM facts ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            {"id": r[0], "subject": r[1], "predicate": r[2], "object": r[3],
             "source_id": r[4], "source_url": r[5], "timestamp": r[6], "version": r[7]}
            for r in rows
        ]

    def detect_conflicts(self, limit: int = 500) -> list[dict]:
        """Find contradictory facts via SQL JOIN (O(n log n))."""
        rows = self.conn.execute("""
            SELECT f1.subject, f1.predicate,
                   f1.id AS fact_a_id, f1.object AS object_a,
                   f1.source_id AS source_a, f1.timestamp AS timestamp_a,
                   f2.id AS fact_b_id, f2.object AS object_b,
                   f2.source_id AS source_b, f2.timestamp AS timestamp_b
            FROM facts f1
            JOIN facts f2 ON f1.subject = f2.subject
                         AND f1.predicate = f2.predicate
                         AND f1.source_id < f2.source_id
                         AND f1.object != f2.object
            ORDER BY f1.subject, f1.predicate
            LIMIT ?
        """, (limit,)).fetchall()
        return [
            {"subject": r[0], "predicate": r[1],
             "fact_a_id": r[2], "object_a": r[3], "source_a": r[4], "timestamp_a": r[5],
             "fact_b_id": r[6], "object_b": r[7], "source_b": r[8], "timestamp_b": r[9]}
            for r in rows
        ]

    def get_fact(self, fact_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, subject, predicate, object, source_id, source_url, timestamp, version "
            "FROM facts WHERE id=?", (fact_id,)
        ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "subject": row[1], "predicate": row[2], "object": row[3],
                "source_id": row[4], "source_url": row[5], "timestamp": row[6], "version": row[7]}

    def close(self) -> None:
        self.conn.close()


class KeeperAgent(BandAgent):
    agent_name = "Keeper"
    agent_description = "Structured fact store. Commands: store, recall, list, detect"

    def __init__(self) -> None:
        super().__init__()
        self.store = KeeperStore()

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        # Parse command: "store subject=X predicate=Y object=Z source=docs"
        if content.startswith("store "):
            await self._cmd_store(content[6:], tools)
            return

        if content.startswith("recall "):
            await self._cmd_recall(content[7:], tools)
            return

        if content == "list" or content == "list all":
            await self._cmd_list(tools)
            return

        if content == "detect":
            await self._cmd_detect(tools)
            return

        if content.startswith("get "):
            await self._cmd_get(content[4:], tools)
            return

        # Help
        await tools.send_message(
            "🤖 Keeper commands:\n"
            "  `store subject=X predicate=Y object=Z source=ID`\n"
            "  `recall <subject>`\n"
            "  `list`\n"
            "  `detect`\n"
            "  `get <id>`"
        )

    async def _cmd_store(self, args: str, tools: AgentToolsProtocol) -> None:
        params = _parse_kv(args)
        subject = params.get("subject") or ""
        predicate = params.get("predicate") or ""
        obj = params.get("object") or ""
        source = params.get("source", "band")

        if not subject or not predicate or not obj:
            await tools.send_message(
                "⚠️ Usage: `store subject=X predicate=Y object=Z source=ID`"
            )
            return

        result = self.store.store(
            subject=subject, predicate=predicate,
            object=obj, source_id=source,
        )
        await tools.send_message(
            f"✅ stored fact #{result['id']}: {subject} → {predicate} = {obj} (from {source})"
        )

    async def _cmd_recall(self, args: str, tools: AgentToolsProtocol) -> None:
        subject = args.strip() or None
        facts = self.store.recall(subject)
        if not facts:
            await tools.send_message(f"📭 No facts for `{args.strip()}`")
            return
        lines = [f"📋 {len(facts)} fact(s):"]
        for f in facts[:15]:
            lines.append(f"  #{f['id']} [{f['source_id']}] {f['subject']} → {f['predicate']} = {f['object']}")
        if len(facts) > 15:
            lines.append(f"  ... and {len(facts) - 15} more")
        await tools.send_message("\n".join(lines))

    async def _cmd_list(self, tools: AgentToolsProtocol) -> None:
        facts = self.store.list_all(limit=25)
        if not facts:
            await tools.send_message("📭 No facts stored yet.")
            return
        lines = [f"📋 {len(facts)} fact(s):"]
        for f in facts:
            lines.append(f"  #{f['id']} [{f['source_id']}] {f['subject']} → {f['predicate']} = {f['object']}")
        await tools.send_message("\n".join(lines))

    async def _cmd_detect(self, tools: AgentToolsProtocol) -> None:
        conflicts = self.store.detect_conflicts()
        if not conflicts:
            await tools.send_message("✅ No conflicts found — all facts are consistent.")
            return
        lines = [f"⚠️ {len(conflicts)} conflict(s) detected:"]
        for c in conflicts:
            lines.append(
                f"  {c['subject']} ({c['predicate']}): "
                f"#{c['fact_a_id']} ({c['source_a']}) vs "
                f"#{c['fact_b_id']} ({c['source_b']})"
            )
        await tools.send_message("\n".join(lines))

    async def _cmd_get(self, args: str, tools: AgentToolsProtocol) -> None:
        try:
            fid = int(args.strip())
        except ValueError:
            await tools.send_message("⚠️ Usage: `get <id>`")
            return
        fact = self.store.get_fact(fid)
        if fact is None:
            await tools.send_message(f"📭 Fact #{fid} not found")
            return
        await tools.send_message(
            f"#{fact['id']} {fact['subject']} → {fact['predicate']} = {fact['object']}\n"
            f"  source: {fact['source_id']} | version: {fact['version']}"
        )


def _parse_kv(text: str) -> dict[str, str]:
    """Parse 'subject=X predicate=Y object=Z' into dict."""
    result = {}
    for part in text.split():
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    KeeperAgent().run()
