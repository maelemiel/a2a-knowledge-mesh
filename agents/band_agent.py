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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from band import Agent as BandAgentRunner
from band.core.simple_adapter import SimpleAdapter
from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Logging config ────────────────────────────────────────────────────
# Suppress noisy SDK loggers — only show WARNING+
for _lib in ("httpx", "band", "phoenix_channels_python_client",
             "band.client", "band.runtime", "band.platform", "band.preprocessing"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

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
        # Stale replay dedup: cutoff is computed per-message, not at init
        self._seen_ids: set[str] = set()
        self._stale_grace: int = 15  # seconds — messages older than this at receive time are stale

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
        Prevents stale replay, self-loop, and agent→agent ping-pong.
        """
        logger.debug(
            "Message from %s (type=%s) in %s: %.80s",
            msg.sender_name or msg.sender_id,
            msg.sender_type,
            room_id,
            msg.content,
        )

        # ── Anti-loop: never react to our own messages ────────────────
        if getattr(msg, "sender_id", None) and msg.sender_id == self._agent_id:
            return

        # ── Stale replay dedup: skip re-delivered messages from reconnect ──
        created = getattr(msg, "created_at", None)
        if isinstance(created, datetime):
            try:
                # Compute cutoff dynamically — every message gets a fresh threshold.
                # This prevents threshold decay on long-running agents.
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._stale_grace)
                if created < cutoff:
                    return
            except TypeError:
                pass

        mid = getattr(msg, "id", None)
        if mid is not None:
            # Periodic cleanup: _seen_ids only needs to cover the stale-replay
            # window (15 s). Clearing it prevents unbounded memory growth on
            # long-running agents.  Stale messages older than the cutoff are
            # already filtered by the timestamp check above.
            if len(self._seen_ids) > 10_000:
                self._seen_ids.clear()
            if str(mid) in self._seen_ids:
                return
            self._seen_ids.add(str(mid))

        if is_session_bootstrap:
            await self.on_bootstrap(room_id, tools)

        # ── Mention routing ───────────────────────────────────────────
        # If sender is an agent, reply to human (breaks agent↔agent loops).
        # If sender is human, reply to them.
        sender_type = getattr(msg, "sender_type", "") or ""
        if sender_type == "User":
            sender = msg.sender_name or ""
        else:
            sender = os.getenv("BAND_USER_HANDLE", "")

        original_send = tools.send_message

        async def _send(content: str, **kwargs: Any) -> Any:
            mentions = kwargs.pop("mentions", None)
            if not mentions and sender:
                mentions = [sender]
            return await original_send(content, mentions=mentions, **kwargs)

        tools.send_message = _send  # type: ignore[method-assign]

        if is_session_bootstrap:
            # Auto-announce + register in the shared room
            hq = os.getenv("BAND_HQ_ROOM_ID", "")
            if hq and room_id == hq:
                await tools.send_message(
                    f"🤖 **{self.agent_name}** en ligne — {self.agent_description}"
                )
                # Self-register with Registry
                reg = os.getenv("BAND_REGISTRY_HANDLE", "registry")
                skills = {"Keeper": "store,recall,list,detect",
                          "Registry": "register,discover,list",
                          "Reconciler": "detect,status,resolve",
                          "Scraper": "slurp-git,slurp-slack,slurp-teams"}.get(
                    self.agent_name, "unknown"
                )
                await tools.send_message(
                    f"@{reg} register name={self.agent_name} skills={skills} "
                    f"description={self.agent_description}"
                )

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
