"""Bridge Agent — mirrors Band room events to the dashboard via HTTP.

No LLM. No filtering. Listens to ALL messages in the room (not just @mentions)
and keeps a rolling buffer that the dashboard HTTP server polls.

Connects to Band via WebSocket (BandAgent) — real-time, no polling.

Usage:
  export BAND_BRIDGE_ID=... BAND_BRIDGE_KEY=... BAND_ROOM_ID=...
  uv run python agents/bridge_agent.py

Or launched by run_mesh.sh.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

# ── Rolling event buffer (thread-safe) ────────────────────────────────

_MAX_EVENTS = 500
_buffer: deque[dict] = deque(maxlen=_MAX_EVENTS)
_buffer_lock = threading.Lock()


class Event:
    """Structured event for the dashboard."""

    def __init__(self, msg_type: str, content: str, sender_id: str = "",
                 sender_name: str = "", timestamp: str | None = None) -> None:
        self.type = msg_type
        self.content = content
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "content": self.content[:500],
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp,
        }


def push_event(event: Event) -> None:
    with _buffer_lock:
        _buffer.append(event.to_dict())


def get_events(limit: int = 50) -> list[dict]:
    with _buffer_lock:
        return list(_buffer)[-limit:]


def clear_events() -> None:
    with _buffer_lock:
        _buffer.clear()


# ── Metrics ───────────────────────────────────────────────────────────

_metrics: dict[str, Any] = {
    "messages_seen": 0,
    "agents_active": 0,
    "last_event_at": "",
}
_metrics_lock = threading.Lock()


def bump_metric(key: str) -> None:
    with _metrics_lock:
        if key in _metrics:
            val = _metrics[key]
            if isinstance(val, int):
                _metrics[key] = val + 1
        _metrics["last_event_at"] = datetime.now(timezone.utc).isoformat()


def get_metrics() -> dict:
    with _metrics_lock:
        return dict(_metrics)


def set_agents_active(n: int) -> None:
    with _metrics_lock:
        _metrics["agents_active"] = n


# ── HTTP Server (stdlib) ──────────────────────────────────────────────

_PORT = int(os.getenv("BRIDGE_PORT", "8765"))


def _run_http_server() -> None:
    """Start stdlib HTTP server for dashboard polling."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, data: Any) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/events":
                limit = int(self.path.split("?limit=")[1]) if "?limit=" in self.path else 50
                self._json(200, {"events": get_events(limit)})
            elif self.path == "/metrics":
                self._json(200, get_metrics())
            elif self.path == "/status":
                self._json(200, {"ok": True, "port": _PORT, "uptime": _metrics.get("messages_seen", 0)})
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug(format, *args)

    server = ThreadingHTTPServer(("0.0.0.0", _PORT), Handler)
    logger.info("Bridge HTTP server on http://0.0.0.0:%d", _PORT)
    server.serve_forever()


# ── Bridge Agent (Band-native) ─────────────────────────────────────────


class BridgeAgent(BandAgent):
    """Passive observer that captures ALL room messages via WebSocket.

    Does NOT reply to messages — silently pushes them to the dashboard buffer.
    Extends BandAgent so it lives INSIDE Band via WebSocket, not outside via REST.
    """

    agent_name = "Bridge"
    agent_description = "Real-time dashboard bridge. Captures all room messages via WebSocket."

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        """Capture every message into the dashboard buffer — no reply."""
        content = msg.content
        sender_id = getattr(msg, "sender_id", "") or ""
        sender_name = getattr(msg, "sender_name", "") or sender_id
        created_at = getattr(msg, "created_at", None)
        timestamp = created_at.isoformat() if isinstance(created_at, datetime) else None

        push_event(Event(
            "message", content,
            sender_id=sender_id,
            sender_name=sender_name,
            timestamp=timestamp,
        ))
        bump_metric("messages_seen")

    async def on_bootstrap(
        self,
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> None:
        """Announce bridge ready — silent, no room spam."""
        push_event(Event("system", "Bridge agent connected", sender_id="bridge"))
        bump_metric("messages_seen")


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    agent_id = os.getenv("BAND_BRIDGE_ID", "")
    api_key = os.getenv("BAND_BRIDGE_KEY", "")

    if not agent_id or not api_key:
        logger.error("BAND_BRIDGE_ID and BAND_BRIDGE_KEY must be set")
        return

    # Start HTTP server in a daemon thread
    http_thread = threading.Thread(target=_run_http_server, daemon=True)
    http_thread.start()

    # Connect to Band via WebSocket (BandAgent.run blocks until stopped)
    bridge = BridgeAgent(agent_id=agent_id, api_key=api_key)
    push_event(Event("system", "Bridge starting — connecting to Band via WebSocket", sender_id="bridge"))
    bridge.run()


if __name__ == "__main__":
    main()
