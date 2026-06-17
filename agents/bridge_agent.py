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

import asyncio
import json
import logging
import os
import sqlite3
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from band import BandLink, RoomPresence
from band.platform.event import MessageEvent, ParticipantAddedEvent, ParticipantRemovedEvent

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent.parent
KEEPER_DB = ROOT / "data" / "keeper.db"
RECONCILER_DB = ROOT / "data" / "reconciler.db"
BRIDGE_DB = ROOT / "data" / "bridge.db"

# ── Rolling event buffer (thread-safe) ────────────────────────────────

_MAX_EVENTS = 500
_buffer: deque[dict] = deque(maxlen=_MAX_EVENTS)
_buffer_lock = threading.Lock()
_seen_event_keys: set[str] = set()


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


def _utc_from_unix(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _payload_value(payload: Any, key: str, default: Any = "") -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _history_conn() -> sqlite3.Connection:
    BRIDGE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BRIDGE_DB), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            sender_id TEXT,
            sender_name TEXT,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
    conn.commit()
    return conn


def append_history(event: Event) -> None:
    data = event.to_dict()
    conn = _history_conn()
    try:
        conn.execute(
            "INSERT INTO events (type, content, sender_id, sender_name, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                data["type"],
                data["content"],
                data["sender_id"],
                data["sender_name"],
                data["timestamp"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(limit: int = 200) -> list[dict]:
    if not BRIDGE_DB.exists():
        return []
    conn = sqlite3.connect(str(BRIDGE_DB), timeout=5)
    try:
        rows = conn.execute(
            "SELECT id, type, content, sender_id, sender_name, timestamp "
            "FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "type": r[1],
            "content": r[2],
            "sender_id": r[3],
            "sender_name": r[4],
            "timestamp": r[5],
        }
        for r in rows
    ]


def clear_history() -> int:
    if not BRIDGE_DB.exists():
        return 0
    conn = sqlite3.connect(str(BRIDGE_DB), timeout=5)
    try:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.execute("DELETE FROM events")
        conn.commit()
        return count
    finally:
        conn.close()


def push_event(event: Event, dedupe_key: str | None = None) -> None:
    with _buffer_lock:
        if dedupe_key:
            if dedupe_key in _seen_event_keys:
                return
            _seen_event_keys.add(dedupe_key)
        _buffer.append(event.to_dict())
    append_history(event)


def get_events(limit: int = 50) -> list[dict]:
    with _buffer_lock:
        return list(_buffer)[-limit:]


def clear_events() -> None:
    with _buffer_lock:
        _buffer.clear()
        _seen_event_keys.clear()


# ── Metrics ───────────────────────────────────────────────────────────

_metrics: dict[str, Any] = {
    "messages_seen": 0,
    "agents_active": 0,
    "last_event_at": "",
    "bridge_status": "starting",
    "last_error": "",
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


def set_bridge_status(status: str, error: str = "") -> None:
    with _metrics_lock:
        _metrics["bridge_status"] = status
        _metrics["last_error"] = error
        _metrics["last_event_at"] = datetime.now(timezone.utc).isoformat()


def get_mesh_state() -> dict[str, Any]:
    """Read live dashboard counters from local SQLite stores."""
    state: dict[str, Any] = {
        "facts_stored": 0,
        "conflicts": 0,
        "resolved": 0,
        "db_status": "ok",
        "db_error": "",
    }

    try:
        current_conflict_pairs: set[tuple[int, int]] = set()
        if KEEPER_DB.exists():
            conn = sqlite3.connect(str(KEEPER_DB), timeout=2)
            try:
                state["facts_stored"] = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                rows = conn.execute("""
                    SELECT f1.id, f2.id
                    FROM facts f1
                    JOIN facts f2 ON f1.subject = f2.subject
                                 AND f1.predicate = f2.predicate
                                 AND f1.source_id < f2.source_id
                                 AND f1.object != f2.object
                """).fetchall()
                current_conflict_pairs = {tuple(sorted((int(a), int(b)))) for a, b in rows}
            finally:
                conn.close()

        resolved_pairs: set[tuple[int, int]] = set()
        if RECONCILER_DB.exists():
            conn = sqlite3.connect(str(RECONCILER_DB), timeout=2)
            try:
                rows = conn.execute(
                    "SELECT fact_a_id, fact_b_id FROM conflicts WHERE status='resolved'"
                ).fetchall()
                resolved_pairs = {tuple(sorted((int(a), int(b)))) for a, b in rows}
            finally:
                conn.close()

        current_resolved_pairs = current_conflict_pairs & resolved_pairs
        state["resolved"] = len(current_resolved_pairs)
        state["conflicts"] = len(current_conflict_pairs - current_resolved_pairs)
    except Exception as e:
        state["db_status"] = "error"
        state["db_error"] = str(e)

    return state


class LocalStateMirror:
    """Mirrors local SQLite state changes into dashboard timeline events."""

    def __init__(self) -> None:
        self._seen_fact_ids: set[int] = set()
        self._seen_keeper_pairs: set[tuple[int, int]] = set()
        self._conflict_status: dict[str, str] = {}

    async def run(self) -> None:
        while True:
            try:
                for event, key in self.poll():
                    push_event(event, dedupe_key=key)
            except Exception as e:
                logger.warning("Local state mirror error: %s", e)
            await asyncio.sleep(1.5)

    def poll(self) -> list[tuple[Event, str]]:
        events: list[tuple[Event, str]] = []
        events.extend(self._poll_facts())
        events.extend(self._poll_keeper_conflicts())
        events.extend(self._poll_reconciler_conflicts())
        return events

    def _poll_facts(self) -> list[tuple[Event, str]]:
        if not KEEPER_DB.exists():
            return []

        conn = sqlite3.connect(str(KEEPER_DB), timeout=2)
        try:
            if not _table_exists(conn, "facts"):
                self._seen_fact_ids.clear()
                return []

            rows = conn.execute(
                "SELECT id, subject, predicate, object, source_id, timestamp "
                "FROM facts ORDER BY id ASC LIMIT 500"
            ).fetchall()
        finally:
            conn.close()

        current_ids = {int(row[0]) for row in rows}
        self._seen_fact_ids &= current_ids

        events: list[tuple[Event, str]] = []
        for fact_id, subject, predicate, obj, source_id, ts in rows:
            fact_id = int(fact_id)
            if fact_id in self._seen_fact_ids:
                continue

            self._seen_fact_ids.add(fact_id)
            content = (
                f"Fact stored #{fact_id}: {subject} -> {predicate} = {obj} "
                f"(source: {source_id})"
            )
            events.append((
                Event("fact", content, sender_id="keeper", sender_name="Keeper",
                      timestamp=_utc_from_unix(ts)),
                f"fact:{fact_id}",
            ))

        return events

    def _resolved_pairs(self) -> set[tuple[int, int]]:
        if not RECONCILER_DB.exists():
            return set()

        conn = sqlite3.connect(str(RECONCILER_DB), timeout=2)
        try:
            if not _table_exists(conn, "conflicts"):
                return set()
            rows = conn.execute(
                "SELECT fact_a_id, fact_b_id FROM conflicts WHERE status='resolved'"
            ).fetchall()
        finally:
            conn.close()

        return {tuple(sorted((int(a), int(b)))) for a, b in rows}

    def _poll_keeper_conflicts(self) -> list[tuple[Event, str]]:
        if not KEEPER_DB.exists():
            return []

        resolved_pairs = self._resolved_pairs()
        conn = sqlite3.connect(str(KEEPER_DB), timeout=2)
        try:
            if not _table_exists(conn, "facts"):
                self._seen_keeper_pairs.clear()
                return []

            rows = conn.execute("""
                SELECT f1.id, f2.id,
                       f1.subject, f1.predicate,
                       f1.object, f1.source_id,
                       f2.object, f2.source_id,
                       MAX(f1.timestamp, f2.timestamp) AS event_ts
                FROM facts f1
                JOIN facts f2 ON f1.subject = f2.subject
                             AND f1.predicate = f2.predicate
                             AND f1.source_id < f2.source_id
                             AND f1.object != f2.object
                ORDER BY event_ts ASC
                LIMIT 500
            """).fetchall()
        finally:
            conn.close()

        current_pairs = {
            tuple(sorted((int(row[0]), int(row[1]))))
            for row in rows
            if tuple(sorted((int(row[0]), int(row[1])))) not in resolved_pairs
        }
        self._seen_keeper_pairs &= current_pairs

        events: list[tuple[Event, str]] = []
        for fact_a, fact_b, subject, predicate, obj_a, source_a, obj_b, source_b, ts in rows:
            pair = tuple(sorted((int(fact_a), int(fact_b))))
            if pair in resolved_pairs or pair in self._seen_keeper_pairs:
                continue

            self._seen_keeper_pairs.add(pair)
            content = (
                f"Conflict detected by Keeper: {subject} ({predicate}) "
                f"#{fact_a} [{source_a}] {obj_a} vs #{fact_b} [{source_b}] {obj_b}"
            )
            events.append((
                Event("conflict", content, sender_id="keeper", sender_name="Keeper",
                      timestamp=_utc_from_unix(ts)),
                f"keeper-conflict:{pair[0]}:{pair[1]}",
            ))

        return events

    def _poll_reconciler_conflicts(self) -> list[tuple[Event, str]]:
        if not RECONCILER_DB.exists():
            return []

        conn = sqlite3.connect(str(RECONCILER_DB), timeout=2)
        try:
            if not _table_exists(conn, "conflicts"):
                self._conflict_status.clear()
                return []

            rows = conn.execute(
                "SELECT id, subject, predicate, status, fact_a_id, fact_b_id, "
                "resolution_fact_id, resolution_reason, created_at, resolved_at, "
                "severity, score_confidence, auto_resolved "
                "FROM conflicts ORDER BY created_at ASC LIMIT 500"
            ).fetchall()
        finally:
            conn.close()

        current_ids = {str(row[0]) for row in rows}
        self._conflict_status = {
            cid: status
            for cid, status in self._conflict_status.items()
            if cid in current_ids
        }

        events: list[tuple[Event, str]] = []
        for row in rows:
            (
                conflict_id,
                subject,
                predicate,
                status,
                fact_a_id,
                fact_b_id,
                resolution_fact_id,
                resolution_reason,
                created_at,
                resolved_at,
                severity,
                score_confidence,
                auto_resolved,
            ) = row
            conflict_id = str(conflict_id)
            previous_status = self._conflict_status.get(conflict_id)
            self._conflict_status[conflict_id] = status

            if previous_status is None and status == "open":
                score = f", confidence {score_confidence:.2f}" if score_confidence is not None else ""
                sev = f"{severity or 'UNKNOWN'}{score}"
                content = (
                    f"Reconciler opened conflict #{conflict_id}: {subject} ({predicate}) "
                    f"facts #{fact_a_id} vs #{fact_b_id} [{sev}]"
                )
                events.append((
                    Event("conflict", content, sender_id="reconciler",
                          sender_name="Reconciler", timestamp=_utc_from_unix(created_at)),
                    f"reconciler-open:{conflict_id}",
                ))
                continue

            changed_to_resolved = status == "resolved" and previous_status != "resolved"
            loaded_as_resolved = previous_status is None and status == "resolved"
            if changed_to_resolved or loaded_as_resolved:
                auto = "auto-resolved" if auto_resolved else "resolved"
                reason = f": {resolution_reason}" if resolution_reason else ""
                content = (
                    f"Conflict #{conflict_id} {auto} -> fact #{resolution_fact_id}"
                    f"{reason}"
                )
                events.append((
                    Event("resolution", content, sender_id="reconciler",
                          sender_name="Reconciler", timestamp=_utc_from_unix(resolved_at)),
                    f"reconciler-resolved:{conflict_id}",
                ))

        return events


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
            if self.path.startswith("/events"):
                limit = int(self.path.split("?limit=")[1]) if "?limit=" in self.path else 50
                self._json(200, {"events": get_events(limit)})
            elif self.path.startswith("/history"):
                limit = int(self.path.split("?limit=")[1]) if "?limit=" in self.path else 200
                self._json(200, {"history": get_history(limit)})
            elif self.path == "/metrics":
                self._json(200, {**get_metrics(), **get_mesh_state()})
            elif self.path == "/status":
                status = {"ok": True, "port": _PORT, **get_metrics(), **get_mesh_state()}
                self._json(200, status)
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path == "/history/clear":
                clear_events()
                count = clear_history()
                self._json(200, {"status": "cleared", "events": count})
                return
            self._json(404, {"error": "not found"})

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug(format, *args)

    server = ThreadingHTTPServer(("0.0.0.0", _PORT), Handler)
    logger.info("Bridge HTTP server on http://0.0.0.0:%d", _PORT)
    server.serve_forever()


# ── Bridge Agent (Band observer) ──────────────────────────────────────


class BridgeAgentAdapter:
    """Observe the Band room and mirror messages plus local DB state."""

    def __init__(self, agent_id: str, api_key: str, room_id: str) -> None:
        self._agent_id = agent_id
        self._api_key = api_key
        self._room_id = room_id
        self._rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")
        self._ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")

    async def run(self) -> None:
        """Listen to Band room events and mirror them to the dashboard buffer."""
        mirror_task = asyncio.create_task(LocalStateMirror().run())
        link = BandLink(
            agent_id=self._agent_id,
            api_key=self._api_key,
            ws_url=self._ws_url,
            rest_url=self._rest_url,
        )
        presence = RoomPresence(
            link,
            room_filter=lambda room: room.get("id") == self._room_id,
            auto_subscribe_existing=True,
        )

        async def on_joined(room_id: str, _payload: dict) -> None:
            set_bridge_status("connected")
            push_event(Event("system", f"Bridge subscribed to room {room_id}", sender_id="bridge"))

        async def on_room_event(room_id: str, event: Any) -> None:
            if room_id != self._room_id:
                return

            payload = getattr(event, "payload", None)
            is_message = isinstance(event, MessageEvent) or getattr(event, "type", "") == "message_created"
            if is_message and payload:
                content = str(_payload_value(payload, "content", ""))
                sender_id = str(_payload_value(payload, "sender_id", ""))
                sender_name = str(_payload_value(payload, "sender_name", "") or sender_id)
                timestamp = _payload_value(payload, "inserted_at", None)
                message_id = str(_payload_value(payload, "id", ""))
                fallback_key = f"{room_id}:{sender_id}:{timestamp}:{content[:80]}"

                push_event(
                    Event(
                        "message",
                        content,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        timestamp=timestamp,
                    ),
                    dedupe_key=f"band-message:{message_id or fallback_key}",
                )
                bump_metric("messages_seen")
                return

            if isinstance(event, ParticipantAddedEvent):
                set_agents_active(get_metrics().get("agents_active", 0) + 1)
                return

            if isinstance(event, ParticipantRemovedEvent):
                set_agents_active(max(get_metrics().get("agents_active", 0) - 1, 0))

        presence.on_room_joined = on_joined
        presence.on_room_event = on_room_event

        try:
            set_bridge_status("connecting")
            push_event(Event("system", "Bridge agent connected", sender_id="bridge"))
            await presence.start()
            if self._room_id in presence.rooms:
                set_bridge_status("connected")
            else:
                msg = (
                    "Bridge connected to Band, but this room was not found for the Bridge agent. "
                    "Add the Bridge agent to BAND_ROOM_ID or fix BAND_ROOM_ID."
                )
                set_bridge_status("no-room", msg)
                push_event(Event("system", msg, sender_id="bridge"))
            await link.run_forever()
        except Exception as e:
            set_bridge_status("error", str(e))
            push_event(Event("system", f"Bridge error: {e}", sender_id="bridge"))
            raise
        finally:
            mirror_task.cancel()
            try:
                await mirror_task
            except asyncio.CancelledError:
                pass
            await presence.stop()
            await link.disconnect()


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

    bridge = BridgeAgentAdapter(agent_id, api_key, room_id)
    push_event(Event("system", "Bridge starting — connecting to Band via WebSocket", sender_id="bridge"))
    await bridge.run()


if __name__ == "__main__":
    asyncio.run(main())
