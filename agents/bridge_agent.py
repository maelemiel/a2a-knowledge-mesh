"""Bridge Agent — mirrors Band room events to the dashboard via HTTP.

No LLM. No filtering. Listens to ALL messages in the room (not just @mentions)
and keeps a rolling buffer that the dashboard HTTP server polls.

Usage:
  export BAND_BRIDGE_ID=... BAND_BRIDGE_KEY=... BAND_ROOM_ID=...
  uv run python agents/bridge_agent.py

Or launched by run_mesh.sh.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

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


# ── Bridge Agent (Band adapter) ────────────────────────────────────────


class BridgeAgentAdapter:
    """Minimal adapter that listens to ALL room messages without an LLM loop.

    Connects to Band via REST polling (no WebSocket SDK dependency for the bridge).
    """

    def __init__(self, agent_id: str, api_key: str, room_id: str) -> None:
        self._agent_id = agent_id
        self._api_key = api_key
        self._room_id = room_id
        self._base_url = os.getenv("BAND_REST_URL", "https://app.band.ai")
        self._last_message_id: str | None = None
        self._running = False

    async def run(self) -> None:
        """Poll Band API for new messages in the room."""
        import httpx

        self._running = True
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}

        async with httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=15) as client:
            push_event(Event("system", "Bridge agent connected", sender_id="bridge"))

            while self._running:
                try:
                    params = {"limit": 20}
                    if self._last_message_id:
                        params["after"] = self._last_message_id

                    resp = await client.get(
                        f"/api/v1/agent/chats/{self._room_id}/messages",
                        params=params,
                    )
                    if resp.is_success:
                        data = resp.json()
                        messages = data.get("data", data.get("messages", []))
                        if isinstance(messages, list):
                            for msg in messages:
                                mid = msg.get("id", "")
                                if mid == self._last_message_id:
                                    continue
                                self._last_message_id = mid
                                sender = msg.get("sender_name") or msg.get("sender_id", "unknown")
                                content = msg.get("content", "")
                                push_event(Event(
                                    "message", content,
                                    sender_id=msg.get("sender_id", ""),
                                    sender_name=sender,
                                    timestamp=msg.get("created_at"),
                                ))
                                bump_metric("messages_seen")
                    else:
                        logger.warning("Bridge poll HTTP %d", resp.status_code)
                except Exception as e:
                    logger.warning("Bridge poll error: %s", e)

                await asyncio.sleep(2.5)  # poll interval (matches dashboard refresh)

    def stop(self) -> None:
        self._running = False


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    agent_id = os.getenv("BAND_BRIDGE_ID", "")
    api_key = os.getenv("BAND_BRIDGE_KEY", "")
    room_id = os.getenv("BAND_ROOM_ID", "")

    if not agent_id or not api_key or not room_id:
        logger.error("BAND_BRIDGE_ID, BAND_BRIDGE_KEY, BAND_ROOM_ID must be set")
        return

    # Start HTTP server in a daemon thread
    http_thread = threading.Thread(target=_run_http_server, daemon=True)
    http_thread.start()

    # Run the polling loop
    bridge = BridgeAgentAdapter(agent_id, api_key, room_id)
    await bridge.run()


if __name__ == "__main__":
    asyncio.run(main())
