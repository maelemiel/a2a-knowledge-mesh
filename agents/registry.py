"""Registry Agent — directory of agent capabilities."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from agents.base import Agent
from protocols.a2a import AgentCard


DB_PATH = Path(__file__).parent.parent / "data" / "registry.db"


class RegistryStore:
    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                card_url TEXT NOT NULL,
                skills TEXT NOT NULL,
                url TEXT NOT NULL,
                last_seen INTEGER NOT NULL
            )
        """)
        self.conn.commit()

    def register(self, agent_id: str, name: str, card_url: str, skills: list[str], url: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO agents (id, name, card_url, skills, url, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, name, card_url, json.dumps(skills), url, int(time.time())),
        )
        self.conn.commit()

    def discover(self, skill: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, card_url, skills, url FROM agents WHERE skills LIKE ?",
            (f"%{skill}%",),
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "card_url": r[2], "skills": json.loads(r[3]), "url": r[4]}
            for r in rows
        ]

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, card_url, skills, url FROM agents ORDER BY last_seen DESC"
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "card_url": r[2], "skills": json.loads(r[3]), "url": r[4]}
            for r in rows
        ]


class RegistryAgent(Agent):
    card = AgentCard(
        name="Registry Agent",
        description="Directory service for A2A agents",
        url="http://localhost:8765",
        skills=["discover", "register", "list"],
    )
    port = 8765

    def __init__(self) -> None:
        super().__init__()
        self.store = RegistryStore()

    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        if method == "register":
            self.store.register(
                agent_id=params["agent_id"],
                name=params["name"],
                card_url=params["card_url"],
                skills=params["skills"],
                url=params["url"],
            )
            return {"status": "registered", "agent_id": params["agent_id"]}

        if method == "discover":
            return {"agents": self.store.discover(params["skill"])}

        if method == "list":
            return {"agents": self.store.list_all()}

        raise ValueError(f"unknown method: {method}")


if __name__ == "__main__":
    RegistryAgent().run()
