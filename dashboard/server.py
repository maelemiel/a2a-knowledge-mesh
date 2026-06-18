"""Dashboard server — proxy requests to Bridge agent.

Simple stdlib HTTP server that serves index.html and proxies
/events, /metrics, /status, and /history to the Bridge agent's HTTP API.

Usage:
  uv run python dashboard/server.py
  # Open http://localhost:8765
"""

from __future__ import annotations

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import sqlite3

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:8765")
PORT = int(os.getenv("DASHBOARD_PORT", "8766"))
HTML_PATH = Path(__file__).parent / "index.html"


def _get_db_metrics() -> dict:
    metrics = {
        "facts_stored": 0,
        "conflicts_total": 0,
        "conflicts_resolved": 0,
        "conflicts_open": 0,
        "registered_agents": []
    }

    # 1. Facts count from keeper.db
    keeper_db = Path(__file__).parent.parent / "data" / "keeper.db"
    if keeper_db.exists():
        try:
            conn = sqlite3.connect(str(keeper_db), timeout=1)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM facts")
            metrics["facts_stored"] = cursor.fetchone()[0]
            conn.close()
        except Exception:
            pass

    # 2. Conflicts from reconciler.db
    reconciler_db = Path(__file__).parent.parent / "data" / "reconciler.db"
    if reconciler_db.exists():
        try:
            conn = sqlite3.connect(str(reconciler_db), timeout=1)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM conflicts")
            metrics["conflicts_total"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM conflicts WHERE status='resolved'")
            metrics["conflicts_resolved"] = cursor.fetchone()[0]
            metrics["conflicts_open"] = metrics["conflicts_total"] - metrics["conflicts_resolved"]
            conn.close()
        except Exception:
            pass

    # 3. Registered agents from registry.db
    registry_db = Path(__file__).parent.parent / "data" / "registry.db"
    if registry_db.exists():
        try:
            conn = sqlite3.connect(str(registry_db), timeout=1)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, skills, description FROM agents")
            for row in cursor.fetchall():
                metrics["registered_agents"].append({
                    "id": row["id"],
                    "name": row["name"],
                    "skills": row["skills"],
                    "description": row["description"]
                })
            conn.close()
        except Exception:
            pass

    return metrics


def _fetch(url: str) -> dict:
    """Fetch JSON from the bridge agent."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def _post(url: str) -> dict:
    """POST to the bridge agent and return its JSON response."""
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, body: str) -> None:
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            if HTML_PATH.exists():
                self._html(200, HTML_PATH.read_text())
            else:
                self._html(200, "<h1>Dashboard</h1><p>index.html not found</p>")
            return

        if self.path.startswith("/events"):
            data = _fetch(f"{BRIDGE_URL}{self.path}")
            self._json(200, data)
            return

        if self.path.startswith("/metrics"):
            data = _fetch(f"{BRIDGE_URL}{self.path}")
            if "error" in data:
                data = {}
            data.update(_get_db_metrics())
            self._json(200, data)
            return

        if self.path.startswith("/status"):
            data = _fetch(f"{BRIDGE_URL}{self.path}")
            self._json(200, data)
            return

        if self.path.startswith("/history"):
            data = _fetch(f"{BRIDGE_URL}{self.path}")
            self._json(200, data)
            return

        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/history/clear":
            data = _post(f"{BRIDGE_URL}/history/clear")
            self._json(200, data)
            return

        if self.path == "/reset":
            self._handle_reset()
            return

        self._json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        """CORS preflight for all routes."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_reset(self) -> None:
        """DELETE FROM local demo databases and clear bridge history."""
        data_dir = Path(__file__).parent.parent / "data"
        cleared = {}
        operations = [
            ("keeper.db", "facts"),
            ("reconciler.db", "conflicts"),
            ("registry.db", "agents"),
        ]
        for db_name, table in operations:
            db_path = data_dir / db_name
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path), timeout=2)
                    conn.execute(f"DELETE FROM {table}")
                    conn.commit()
                    cleared[db_name] = f"{table} table cleared"
                    conn.close()
                except Exception as exc:
                    cleared[db_name] = f"error: {exc}"
            else:
                cleared[db_name] = "db not found (skipped)"

        cleared["bridge"] = _post(f"{BRIDGE_URL}/history/clear")
        self._json(200, {"status": "ok", "message": "Demo state cleared", "details": cleared})

    def log_message(self, format: str, *args: Any) -> None:
        pass  # quiet


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Dashboard: http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
