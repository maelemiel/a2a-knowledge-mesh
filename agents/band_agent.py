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
import re
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
# Mention stripping — handles Band display names with spaces and accents
# ---------------------------------------------------------------------------


def _strip_mentions(raw: str, sender_name: str) -> str:
    """Strip leading @mentions from a message, handling multi-word display names.

    Band delivers mentions like ``@Maël Perrigaud/scraper scan self`` where
    the display name can contain spaces and Unicode characters.  This function
    strips the entire mention prefix so the command (e.g. ``"scan self"``)
    lands cleanly in the subclass handler.

    Strategy (in order):

    1. If we know the sender's display name, strip ``@<sender_name>`` plus an
       optional ``/<agent_name>`` suffix.
    2. Strip Band-encoded ``@[[UUID]]`` mentions (internal platform format).
    3. Then greedily strip any remaining simple ``@word`` mentions (no spaces).
    4. As a last resort, use a regex that handles an unknown sender with a
       multi-word display name and a ``/agent`` anchor.

    Returns the stripped content.
    """
    stripped = raw

    # ── Step 1: strip sender's display name mention ───────────────
    if sender_name:
        mention = f"@{sender_name}"
        if stripped.startswith(mention):
            stripped = stripped[len(mention):]
            # Strip /agent suffix if present (e.g. "/scraper")
            if stripped.startswith("/"):
                slash_end = stripped.find(" ")
                if slash_end == -1:
                    stripped = ""
                else:
                    stripped = stripped[slash_end:].lstrip()
            else:
                stripped = stripped.lstrip()

    # ── Step 2: strip Band-encoded @[[UUID]] mentions ───────────
    while stripped:
        m = re.match(r"^@\[\[[a-f0-9-]+\]\]\s*", stripped)
        if m:
            stripped = stripped[m.end():].lstrip()
        else:
            break

    # ── Step 3: strip remaining simple @word mentions ─────────────
    while stripped:
        m = re.match(r"^@(\S+)\s*", stripped)
        if m:
            stripped = stripped[m.end():].lstrip()
        else:
            break

    # ── Step 4: fallback regex for mentions with /agent anchor ────
    # When sender_name is unknown or doesn't match, the /agent suffix is
    # the only reliable signal that a multi-word display name precedes it.
    # Greedy match: @ followed by non-/ words, optional /agent suffix.
    if stripped != raw and "/" not in raw:
        # Already stripped partially, no /agent to anchor further stripping.
        return stripped

    m = re.match(r"^@[^/\s]+(?:\s+[^/\s]+)*(?:/\S+)?\s*", raw)
    if m:
        stripped = raw[m.end():].lstrip()

    return stripped


def _replace_content(msg: PlatformMessage, new_content: str) -> PlatformMessage:
    """Return a copy of *msg* with ``content`` replaced.

    Uses ``dataclasses.replace`` when available; falls back to ``setattr``
    on objects that are not frozen dataclasses.
    """
    try:
        from dataclasses import replace
        return replace(msg, content=new_content)  # type: ignore[call-arg]
    except (TypeError, ValueError):
        # PlatformMessage might not be a dataclass — fall back to setattr
        msg.content = new_content  # type: ignore[attr-defined]
        return msg


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
        # Snapshot the class-level description BEFORE the Band SDK overwrites
        # the instance attribute in SimpleAdapter (it sets agent_name/description
        # from platform metadata, which can be empty).
        self._local_description: str = self.agent_description

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

        # ── Strip leading @mentions BEFORE any subclass processing ───
        # Robust: uses sender_name to handle "Maël Perrigaud" (spaces, accents)
        # and the /agent suffix (e.g. "@/Maël Perrigaud/scraper").
        raw = msg.content
        logger.info("[%s] Raw message content: %r", self.agent_name, raw)
        sender_name = getattr(msg, "sender_name", "") or ""
        stripped = _strip_mentions(raw, sender_name)
        if stripped != raw:
            msg = _replace_content(msg, stripped)
        logger.info("[%s] Processed message content: %r", self.agent_name, msg.content)

        # ── Empty message guard: bare @mention with no command ──────
        if not msg.content.strip():
            logger.info(
                "[%s] Empty message after mention stripping — ignoring bare mention from %s",
                self.agent_name,
                sender_name or "unknown",
            )
            return

        # ── Build the safe send_message wrapper ──────────────────────
        # Rule: Band rejects messages with empty mentions.  Fallback chain:
        #   1. Explicit mentions passed by caller (highest priority)
        #   2. Human user handle (BAND_USER_HANDLE env var)
        #   3. Original sender's display name (last resort)
        sender_type = getattr(msg, "sender_type", "") or ""
        if sender_type == "User":
            sender = msg.sender_name or ""
        else:
            # Agent → reply to human, never to another agent (loop prevention)
            sender = os.getenv("BAND_USER_HANDLE", "") or msg.sender_name or ""

        if not sender:
            logger.warning(
                "[%s] No mention target: BAND_USER_HANDLE not set and sender_name empty. "
                "Band will reject messages without mentions.",
                self.agent_name,
            )

        original_send = tools.send_message

        async def _send(content: str, **kwargs: Any) -> Any:
            mentions = kwargs.pop("mentions", None)
            if not mentions:
                mentions = [sender] if sender else []
            return await original_send(content, mentions=mentions, **kwargs)

        tools.send_message = _send  # type: ignore[method-assign]

        # ── Bootstrap: on first connect ──────────────────────────────
        if is_session_bootstrap:
            await self.on_bootstrap(room_id, tools)

            # Self-register in the shared room (silent — no "en ligne" spam)
            hq = os.getenv("BAND_HQ_ROOM_ID", "")
            if hq and room_id == hq:
                # Registry does NOT self-register (Band rejects cannot_mention_self).
                # Explicit mention required because Band ignores text @mentions.
                if self.agent_name.lower() != "registry":
                    reg = resolve_handle("BAND_REGISTRY_HANDLE", "registry")
                    agent_key = self.agent_name.lower()
                    skills = {
                        "keeper": "store,recall,list,detect",
                        "registry": "register,discover,list",
                        "reconciler": "detect,status,resolve",
                        "scraper": "scan,status",
                    }.get(agent_key, "unknown")
                    await tools.send_message(
                        f"@{reg} register name={self.agent_name} skills={skills} "
                        f"description={self.agent_description}",
                        mentions=[reg],
                    )

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
        """Called after the agent connects to Band.

        The SDK may have already set ``self.agent_description`` to the platform
        value (possibly empty).  We use ``_local_description`` (snapshot from
        ``__init__``) to decide whether to accept the platform version.
        """
        self.agent_name = agent_name
        if self._local_description == "A2A Knowledge Mesh agent" and agent_description:
            self.agent_description = agent_description
        else:
            # Restore our rich local description (SDK may have cleared it)
            self.agent_description = self._local_description or agent_description
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


def resolve_handle(env_var: str, agent_name: str) -> str:
    """Resolve an agent handle for inter-agent @mentions.

    Band uses the canonical format ``username/agent-slug`` (e.g.
    ``mael2perso/keeper``).  This helper:

    1. Reads the env var (e.g. ``BAND_KEEPER_HANDLE``) for an explicit override.
    2. Falls back to ``{BAND_USER_HANDLE}/{agent_name}`` if the user handle is set.
    3. Last resort: the bare *agent_name* (for sibling agents / global agents).

    Always include the full handle when ``BAND_USER_HANDLE`` is configured —
    Band's contact tools require handle-based addressing.
    """
    explicit = os.getenv(env_var, "")
    if explicit:
        return explicit
    user_handle = os.getenv("BAND_USER_HANDLE", "")
    if user_handle:
        return f"{user_handle}/{agent_name}"
    logger.warning(
        "BAND_USER_HANDLE not set — using bare agent name %r for mentions. "
        "Band may not resolve this correctly for cross-account agents.",
        agent_name,
    )
    return agent_name
