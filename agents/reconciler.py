"""Reconciler Agent — detect contradictions, create Band room, resolve."""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from agents.base import Agent
from protocols.a2a import AgentCard


DB_PATH = Path(__file__).parent.parent / "data" / "reconciler.db"


class ReconcilerStore:
    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conflicts (
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
                resolved_at INTEGER
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)")
        self.conn.commit()

    def create_conflict(
        self, subject: str, predicate: str,
        fact_a_id: int, fact_b_id: int,
        source_a: str, source_b: str,
    ) -> dict:
        conflict_id = str(uuid.uuid4())[:8]
        ts = int(time.time())
        self.conn.execute(
            "INSERT INTO conflicts (id, subject, predicate, fact_a_id, fact_b_id, source_a, source_b, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conflict_id, subject, predicate, fact_a_id, fact_b_id, source_a, source_b, ts),
        )
        self.conn.commit()
        return {"conflict_id": conflict_id, "subject": subject, "predicate": predicate}

    def resolve(self, conflict_id: str, resolution_fact_id: int, reason: str) -> dict:
        ts = int(time.time())
        self.conn.execute(
            "UPDATE conflicts SET status='resolved', resolution_fact_id=?, resolution_reason=?, resolved_at=? WHERE id=?",
            (resolution_fact_id, reason, ts, conflict_id),
        )
        self.conn.commit()
        return {"conflict_id": conflict_id, "status": "resolved"}

    def get_open(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, fact_a_id, fact_b_id, source_a, source_b, band_room_id, created_at "
            "FROM conflicts WHERE status='open' ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "conflict_id": r[0], "subject": r[1], "predicate": r[2],
                "fact_a_id": r[3], "fact_b_id": r[4],
                "source_a": r[5], "source_b": r[6],
                "band_room_id": r[7], "created_at": r[8],
            }
            for r in rows
        ]

    def get_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, status, created_at, resolved_at "
            "FROM conflicts ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "conflict_id": r[0], "subject": r[1], "predicate": r[2],
                "status": r[3], "created_at": r[4], "resolved_at": r[5],
            }
            for r in rows
        ]

    def set_band_room(self, conflict_id: str, room_id: str) -> None:
        self.conn.execute(
            "UPDATE conflicts SET band_room_id=? WHERE id=?",
            (room_id, conflict_id),
        )
        self.conn.commit()


BAND_API_BASE = "https://api.band.ai/v2"


class BandClient:
    """Minimal Band REST API client."""

    def __init__(self, agent_id: str, api_key: str) -> None:
        self.agent_id = agent_id
        self.client = httpx.Client(
            base_url=BAND_API_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def create_room(self, title: str) -> dict | None:
        resp = self.client.post(
            f"/agents/{self.agent_id}/rooms",
            json={"title": title},
        )
        if resp.is_error:
            return None
        return resp.json()

    def post_message(self, room_id: str, message: str) -> dict | None:
        resp = self.client.post(
            f"/agents/{self.agent_id}/rooms/{room_id}/messages",
            json={"content": message},
        )
        if resp.is_error:
            return None
        return resp.json()


class ReconcilerAgent(Agent):
    card = AgentCard(
        name="Reconciler Agent",
        description="Detects contradictory facts and resolves them via Band",
        url="http://localhost:8767",
        skills=["detect-conflict", "resolve", "status"],
    )
    port = 8767

    def __init__(self, band_agent_id: str | None = None, band_api_key: str | None = None) -> None:
        super().__init__()
        self.store = ReconcilerStore()
        self.keeper_url = "http://localhost:8766"
        self.band = None
        if band_agent_id and band_api_key:
            self.band = BandClient(band_agent_id, band_api_key)

    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        if method == "detect-conflict":
            return self._detect(params)

        if method == "resolve":
            return self.store.resolve(
                conflict_id=params["conflict_id"],
                resolution_fact_id=params["resolution_fact_id"],
                reason=params.get("reason", ""),
            )

        if method == "status":
            return {"open": self.store.get_open(), "all": self.store.get_all()}

        raise ValueError(f"unknown method: {method}")

    def _detect(self, params: dict[str, Any]) -> dict:
        """Scan a keeper for contradictions on the same (subject, predicate)."""
        keeper_facts = self._fetch_keeper(params.get("keeper_url", self.keeper_url))
        if not keeper_facts:
            return {"conflicts": [], "message": "no facts to compare"}

        grouped: dict[str, list[dict]] = {}
        for f in keeper_facts:
            key = f"{f['subject']}|{f['predicate']}"
            grouped.setdefault(key, []).append(f)

        created: list[dict] = []
        for key, facts in grouped.items():
            if len(facts) < 2:
                continue
            # Different source_id + different object = contradiction
            for i in range(len(facts)):
                for j in range(i + 1, len(facts)):
                    a, b = facts[i], facts[j]
                    if a["source_id"] == b["source_id"]:
                        continue
                    if a["object"] == b["object"]:
                        continue
                    conflict = self.store.create_conflict(
                        subject=a["subject"],
                        predicate=a["predicate"],
                        fact_a_id=a["id"],
                        fact_b_id=b["id"],
                        source_a=a["source_id"],
                        source_b=b["source_id"],
                    )
                    created.append(conflict)

                    # Post to Band if configured
                    if self.band:
                        self._notify_band(a, b, conflict)

        return {"conflicts": created, "count": len(created)}

    def _fetch_keeper(self, keeper_url: str) -> list[dict] | None:
        try:
            with httpx.Client() as client:
                resp = client.post(
                    urljoin(keeper_url, "/a2a"),
                    json={
                        "jsonrpc": "2.0",
                        "id": "reconciler-scan",
                        "method": "list-facts",
                        "params": {"limit": 1000},
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("result", {}).get("facts", [])
        except Exception:
            return None

    def _notify_band(self, a: dict, b: dict, conflict: dict) -> None:
        if not self.band:
            return
        title = f"Conflict: {a['subject']} ({a['predicate']})"
        room = self.band.create_room(title)
        if not room:
            return

        room_id = room.get("id", "")
        self.store.set_band_room(conflict["conflict_id"], room_id)

        message = (
            f"**Conflict detected**\n"
            f"- **Subject:** {a['subject']}\n"
            f"- **Predicate:** {a['predicate']}\n\n"
            f"**Fact A** ({a['source_id']}): {a['object']}\n"
            f"**Fact B** ({b['source_id']}): {b['object']}\n\n"
            f"Resolve via `reconciler.resolve(conflict_id='{conflict['conflict_id']}', ...)`"
        )
        self.band.post_message(room_id, message)


if __name__ == "__main__":
    import os
    band_id = os.getenv("BAND_AGENT_ID")
    band_key = os.getenv("BAND_API_KEY")
    ReconcilerAgent(band_agent_id=band_id, band_api_key=band_key).run()
