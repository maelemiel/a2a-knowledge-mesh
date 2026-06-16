"""Registry Agent — Band-native discovery service.

Agents announce themselves by posting their AgentCard in the discovery room.
Other agents discover capabilities by asking in the room.

Commands:
  @registry register name=X skills=X,Y,Z
  @registry discover <skill>
  @registry list
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "registry.db"


class RegistryStore:
    """SQLite-backed agent registry."""

    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                skills TEXT NOT NULL,
                description TEXT,
                card_url TEXT,
                last_seen INTEGER NOT NULL
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_skills ON agents(skills)"
        )
        self.conn.commit()

    def register(self, agent_id: str, name: str, skills: list[str],
                 description: str = "", card_url: str = "") -> dict:
        ts = int(time.time())
        self.conn.execute(
            "INSERT OR REPLACE INTO agents (agent_id, name, skills, description, card_url, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, name, json.dumps(skills), description, card_url, ts),
        )
        self.conn.commit()
        return {"agent_id": agent_id, "status": "registered"}

    def discover(self, skill: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT agent_id, name, skills, description, card_url FROM agents"
        ).fetchall()
        result = []
        for r in rows:
            agent_id, name, skills_json, desc, card_url = r
            skills = json.loads(skills_json)
            if any(skill in s for s in skills):
                result.append({
                    "agent_id": agent_id, "name": name,
                    "skills": skills, "description": desc, "card_url": card_url,
                })
        return result

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT agent_id, name, skills, description, card_url, last_seen "
            "FROM agents ORDER BY last_seen DESC"
        ).fetchall()
        return [
            {"agent_id": r[0], "name": r[1], "skills": json.loads(r[2]),
             "description": r[3], "card_url": r[4], "last_seen": r[5]}
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()


class RegistryAgent(BandAgent):
    agent_name = "Registry"
    agent_description = "Agent directory. Commands: register, discover, list"

    def __init__(self) -> None:
        super().__init__()
        self.store = RegistryStore()

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        if content.startswith("register "):
            await self._cmd_register(content[9:], tools)
            return

        if content.startswith("discover "):
            await self._cmd_discover(content[9:], tools)
            return

        if content == "list":
            await self._cmd_list(tools)
            return

        await tools.send_message(
            "🤖 Registry commands:\n"
            "  `register name=X skills=X,Y,Z description=...`\n"
            "  `discover <skill>`\n"
            "  `list`"
        )

    async def _cmd_register(self, args: str, tools: AgentToolsProtocol) -> None:
        params = _parse_kv(args)
        agent_id = params.get("name", "").lower().replace(" ", "-")
        name = params.get("name", agent_id)
        skills_raw = params.get("skills", "")
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
        description = params.get("description", "")

        if not agent_id or not skills:
            await tools.send_message(
                "⚠️ Usage: `register name=X skills=X,Y,Z description=...`"
            )
            return

        self.store.register(
            agent_id=agent_id, name=name,
            skills=skills, description=description,
        )
        await tools.send_message(
            f"✅ Registered `{name}` with skills: {', '.join(skills)}"
        )

    async def _cmd_discover(self, args: str, tools: AgentToolsProtocol) -> None:
        skill = args.strip()
        agents = self.store.discover(skill)
        if not agents:
            await tools.send_message(f"🔍 No agents found with skill `{skill}`")
            return
        lines = [f"🔍 Agents with `{skill}`:"]
        for a in agents:
            lines.append(f"  • `{a['name']}` — {', '.join(a['skills'])}")
            if a.get("description"):
                lines.append(f"    {a['description']}")
        await tools.send_message("\n".join(lines))

    async def _cmd_list(self, tools: AgentToolsProtocol) -> None:
        agents = self.store.list_all()
        if not agents:
            await tools.send_message("📭 No agents registered.")
            return
        lines = [f"📋 {len(agents)} registered agent(s):"]
        for a in agents:
            lines.append(f"  • `{a['name']}` ({', '.join(a['skills'])})")
        await tools.send_message("\n".join(lines))


def _parse_kv(text: str) -> dict[str, str]:
    """Parse 'key=val key2=val2' into dict."""
    result = {}
    for part in text.split():
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    RegistryAgent().run()
