"""Registry Agent — Band-native discovery service.

Agents announce themselves by posting their AgentCard in the discovery room.
Other agents discover capabilities by asking in the room.

Commands:
  @registry register name=X skills=X,Y,Z
  @registry discover <skill>
  @registry list
"""

from __future__ import annotations

import logging

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent
from agents.registry import RegistryStore

logger = logging.getLogger(__name__)


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
            await tools.send_message("⚠️ Usage: `register name=X skills=X,Y,Z description=...`")
            return

        self.store.register(
            agent_id=agent_id,
            name=name,
            skills=skills,
            description=description,
        )
        await tools.send_message(f"✅ Registered `{name}` with skills: {', '.join(skills)}")

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
