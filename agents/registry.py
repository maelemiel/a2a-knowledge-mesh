"""Registry Agent — directory service with authenticated registration.

- Registers agents by role token (only ``keeper`` / ``reconciler`` roles
  may self-register; external clients must use master token).
- ``register`` and ``discover`` are authenticated via ``A2AAuthMiddleware``.
- Input validated via Pydantic schemas.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from agents.base import Agent
from agents.validation import DiscoverParams, RegisterParams
from protocols.a2a import AgentCard

logger = logging.getLogger(__name__)


DB_PATH = Path(__file__).parent.parent / "data" / "registry.db"


class RegistryStore:
    """SQLite-backed registry of known agents.

    Optimised for concurrent reads (WAL mode) with indexed lookups by skill.
    """

    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                card_url TEXT,
                skills TEXT NOT NULL,   -- JSON array
                url TEXT,
                last_seen INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'agent',
                description TEXT
            )
        """)
        # Composite index for LIKE-based skill search
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_skills ON agents(skills)")
        self.conn.commit()
        logger.info("RegistryStore ready at %s", db_path)

    def register(
        self,
        agent_id: str,
        name: str,
        skills: list[str],
        card_url: str | None = None,
        url: str | None = None,
        role: str = "agent",
        description: str | None = None,
    ) -> dict:
        ts = int(time.time())
        self.conn.execute(
            "INSERT OR REPLACE INTO agents (id, name, card_url, skills, url, last_seen, role, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, name, card_url, json.dumps(skills), url, ts, role, description),
        )
        self.conn.commit()
        logger.info("Agent %r (role=%r) registered at %s", agent_id, role, url)
        return {"agent_id": agent_id, "status": "registered"}

    def unregister(self, agent_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def discover(self, skill: str) -> list[dict]:
        """Search agents by skill name (substring match on JSON column).

        Uses ``LIKE`` on the raw JSON string — fast enough for <10K agents.
        For scale, normalise skills into a separate table.
        """
        if not skill:
            return self.list_all()
        rows = self.conn.execute(
            "SELECT id, name, card_url, skills, url, role, description FROM agents WHERE skills LIKE ?",
            (f"%{skill}%",),
        ).fetchall()
        return [
            {
                "id": r[0],
                "agent_id": r[0],
                "name": r[1],
                "card_url": r[2],
                "skills": json.loads(r[3]),
                "url": r[4],
                "role": r[5],
                "description": r[6],
            }
            for r in rows
        ]

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, card_url, skills, url, role, description FROM agents ORDER BY last_seen DESC"
        ).fetchall()
        return [
            {
                "id": r[0],
                "agent_id": r[0],
                "name": r[1],
                "card_url": r[2],
                "skills": json.loads(r[3]),
                "url": r[4],
                "role": r[5],
                "description": r[6],
            }
            for r in rows
        ]

    def get_by_id(self, agent_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, name, card_url, skills, url, role, description FROM agents WHERE id=?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "agent_id": row[0],
            "name": row[1],
            "card_url": row[2],
            "skills": json.loads(row[3]),
            "url": row[4],
            "role": row[5],
            "description": row[6],
        }

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Authorised roles that may self-register
# ---------------------------------------------------------------------------
ALLOWED_SELF_REGISTER_ROLES = frozenset({"keeper", "reconciler", "registry"})


class RegistryAgent(Agent):
    card = AgentCard(
        name="Registry Agent",
        description="Directory service for A2A agents",
        url="http://localhost:8765",
        skills=["discover", "register", "list", "unregister"],
        authentication={"schemes": [{"type": "bearer"}]},
    )
    port = 8765
    agent_role = "registry"

    def __init__(self) -> None:
        super().__init__()
        self.store = RegistryStore()
        self.connection = self.store.conn

    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        if method == "register":
            return self._register(params)

        if method == "unregister":
            return self._unregister(params)

        if method == "discover":
            return self._discover(params)

        if method == "list":
            return {"agents": self.store.list_all()}

        raise ValueError(f"unknown method: {method}")

    def _register(self, params: dict) -> dict:
        p = RegisterParams(**params)
        # Permission check: only specific agent roles can self-register
        # External callers must use the master token (already verified by middleware)
        if p.agent_id not in ALLOWED_SELF_REGISTER_ROLES:
            raise PermissionError(
                f"Agent '{p.agent_id}' is not authorised to self-register. "
                f"Allowed self-register IDs: {', '.join(sorted(ALLOWED_SELF_REGISTER_ROLES))}"
            )
        self.store.register(
            agent_id=p.agent_id,
            name=p.name,
            card_url=p.card_url,
            skills=p.skills,
            url=p.url,
            role=p.agent_id,
        )
        return {"status": "registered", "agent_id": p.agent_id}

    def _unregister(self, params: dict) -> dict:
        agent_id = params.get("agent_id", "")
        if not agent_id:
            raise ValueError("missing 'agent_id'")
        removed = self.store.unregister(agent_id)
        return {"status": "unregistered" if removed else "not_found", "agent_id": agent_id}

    def _discover(self, params: dict) -> dict:
        p = DiscoverParams(**params)
        return {"agents": self.store.discover(p.skill)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    RegistryAgent().run()
