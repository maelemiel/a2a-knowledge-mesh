"""MeshAgent — single Band agent routing all 3 handlers.

Replaces 3 separate Band agents (registry_band, keeper_band, reconciler_band)
with one. Band enforces 1 WebSocket per agent ID.

Routes by message prefix:
  register|discover  → Registry
  store|recall|list  → Keeper
  detect|status|resolve → Reconciler
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent
from agents.registry_band import RegistryAgent
from agents.keeper_band import KeeperAgent
from agents.reconciler_band import ReconcilerAgent

logger = logging.getLogger(__name__)


class MeshAgent(BandAgent):
    agent_name = "A2A Knowledge Mesh"
    agent_description = ("3-in-1 mesh agent: Registry (register/discover/list), "
                         "Keeper (store/recall/list), Reconciler (detect/status/resolve)")

    def __init__(self) -> None:
        super().__init__()
        self.registry = RegistryAgent()
        self.keeper = KeeperAgent()
        self.reconciler = ReconcilerAgent(
            keeper_db=str(Path(__file__).parent.parent / "data" / "keeper.db")
        )

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        # Strip leading @mentions from content (e.g. "@A2A Knowledge Mesh store ..." → "store ...")
        parts = content.split()
        while parts and parts[0].startswith("@"):
            parts.pop(0)
        stripped = " ".join(parts)

        # Create a new message with stripped content for the handlers
        clean_msg = replace(msg, content=stripped) if stripped != content else msg

        first_word = stripped.lower().split()[0] if stripped else ""

        if first_word in ("register", "discover"):
            await self.registry.handle_message(clean_msg, tools, room_id)

        elif first_word in ("store", "recall", "list", "detect", "get"):
            await self.keeper.handle_message(clean_msg, tools, room_id)

        elif first_word in ("detect", "status", "resolve"):
            await self.reconciler.handle_message(clean_msg, tools, room_id)

        else:
            await tools.send_message(
                "🤖 A2A Knowledge Mesh commands:\n\n"
                "**Registry:**\n"
                "  `register name=X skills=X,Y,Z`\n"
                "  `discover <skill>`\n"
                "  `list`\n\n"
                "**Keeper:**\n"
                "  `store subject=X predicate=Y object=Z source=ID`\n"
                "  `recall <subject>`\n"
                "  `list`\n"
                "  `get <id>`\n\n"
                "**Reconciler:**\n"
                "  `detect`\n"
                "  `status`\n"
                "  `resolve <id> <fact_id> [reason]`"
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    MeshAgent().run()
