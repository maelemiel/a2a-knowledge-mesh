"""BandAgent — base class for A2A Knowledge Mesh agents running on Band.

Each agent is a ``SimpleAdapter`` that connects to Band via WebSocket.
The SDK handles reconnection, heartbeats, room lifecycle automatically.

Agents communicate through Band rooms with @mentions.
No HTTP servers. No JSON-RPC. Band is the mesh.

Usage::

    class KeeperAgent(BandAgent):
        async def handle_message(self, msg, tools, room_id):
            if msg.content.startswith("store "):
                # parse, store in SQLite, reply in room
                await tools.send_message("Fact stored: ...")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from band import Agent as BandAgentRunner
from band.core.simple_adapter import SimpleAdapter
from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BandAgent — base class for all mesh agents
# ---------------------------------------------------------------------------


class BandAgent(SimpleAdapter[Any]):
    """Base class for A2A Knowledge Mesh agents on Band.

    Subclasses override ``handle_message()``.
    The SDK calls ``on_message()`` when the agent is @mentioned in a room.
    Use ``tools.send_message()`` to reply.
    """

    # Agent metadata — override in subclass
    agent_name: str = "agent"
    agent_description: str = "A2A Knowledge Mesh agent"

    def __init__(self, agent_id: str | None = None, api_key: str | None = None) -> None:
        super().__init__()
        self._band_runner: BandAgentRunner | None = None
        self._agent_id = agent_id or ""
        self._api_key = api_key or ""

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Called by the SDK when the agent receives a message.

        Delegates to ``handle_message()`` which subclasses override.
        Auto-@mentions the sender in replies (required by Band).
        """
        logger.debug(
            "Message from %s in %s: %.80s",
            msg.sender_name or msg.sender_id,
            room_id,
            msg.content,
        )

        if is_session_bootstrap:
            await self.on_bootstrap(room_id, tools)

        # Wrap tools to auto-@mention the sender in every reply
        sender = msg.sender_name
        original_send = tools.send_message

        async def _send(content: str, **kwargs: Any) -> Any:
            mentions = kwargs.pop("mentions", None)
            if not mentions and sender:
                mentions = [sender]
            return await original_send(content, mentions=mentions, **kwargs)

        tools.send_message = _send  # type: ignore[method-assign]

        # Strip leading @mentions from content before passing to handler
        # (e.g. "@Keeper store ..." → "store ...")
        raw = msg.content
        parts = raw.split()
        while parts and parts[0].startswith("@"):
            parts.pop(0)
        if parts != raw.split():
            from dataclasses import replace
            msg = replace(msg, content=" ".join(parts))

        await self.handle_message(msg, tools, room_id)

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        """Override this in subclass to handle incoming messages."""
        pass

    async def on_bootstrap(
        self,
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> None:
        """Called when the agent first joins a room."""
        pass

    async def on_cleanup(self, room_id: str) -> None:
        """Called when the agent leaves a room."""
        logger.info("Cleaned up room %s", room_id)

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Called after the agent connects to Band."""
        self.agent_name = agent_name
        self.agent_description = agent_description
        logger.info("Agent %r connected to Band", agent_name)

    # ----------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------

    def connect(
        self,
        agent_id: str | None = None,
        api_key: str | None = None,
        *,
        ws_url: str = "wss://app.band.ai/api/v1/socket/websocket",
        rest_url: str = "https://app.band.ai",
    ) -> BandAgentRunner:
        """Create and return the Band Agent runner.

        Call ``agent.run()`` to start the WebSocket connection.
        """
        self._agent_id = agent_id or self._agent_id or os.getenv("BAND_AGENT_ID", "")
        self._api_key = api_key or self._api_key or os.getenv("BAND_API_KEY", "")

        if not self._agent_id or not self._api_key:
            raise ValueError(
                "Agent ID and API key required. "
                "Set BAND_AGENT_ID and BAND_API_KEY env vars or pass as args."
            )

        self._band_runner = BandAgentRunner.create(
            adapter=self,
            agent_id=self._agent_id,
            api_key=self._api_key,
            ws_url=ws_url,
            rest_url=rest_url,
        )
        return self._band_runner

    def run(
        self,
        agent_id: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Connect to Band and start listening.

        Blocks until the agent is stopped.
        """
        import asyncio

        runner = self.connect(agent_id, api_key, **kwargs)
        asyncio.run(runner.run())


# ---------------------------------------------------------------------------
# Utility: mention formatting
# ---------------------------------------------------------------------------


def mention(agent_name: str) -> str:
    """Return an @mention string for an agent."""
    return f"@{agent_name}"
