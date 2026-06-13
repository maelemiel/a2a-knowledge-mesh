"""Shared A2A Agent Card server.
Reusable HTTP server serving /.well-known/agent-card.json + JSON-RPC 2.0."""

import json, logging, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)


def make_card(name: str, desc: str, version: str, port: int, skills: list[dict],
              extra_caps: dict | None = None) -> dict:
    return {
        "name": name,
        "description": desc,
        "version": version,
        "url": f"http://localhost:{port}/a2a",
        "capabilities": {"streaming": False, "pushNotifications": False, **(extra_caps or {})},
        "skills": skills,
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "securitySchemes": [],
    }


def make_handler(card: dict, handler):
    """Create an HTTP handler class for a given Agent Card and message handler.
    handler(card, text) -> response string"""
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.path == "/.well-known/agent-card.json":
                    self._json(200, card)
                else:
                    self.send_response(404); self.end_headers()
            except Exception as e:
                logging.getLogger(card['name']).error(f"GET error: {e}")
                self.send_response(500); self.end_headers()

        def do_POST(self):
            try:
                if self.path == "/a2a":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length))
                    req, rid = body, body.get("id")
                    method = req.get("method")

                    if method == "message/send":
                        parts = req.get("params", {}).get("message", {}).get("parts", [])
                        text = parts[0].get("text", "") if parts else ""
                        try:
                            result = handler(card, text)
                            self._json(200, {
                                "jsonrpc": "2.0", "id": rid,
                                "result": {
                                    "task": {
                                        "id": f"t-{rid}", "status": "completed",
                                        "message": {"role": "agent", "parts": [{"type": "text", "text": result}]}
                                    }
                                }
                            })
                        except Exception as e:
                            logging.getLogger(card['name']).error(f"Handler error: {e}")
                            self._json(200, {
                                "jsonrpc": "2.0", "id": rid,
                                "error": {"code": -32603, "message": f"Internal error: {e}"}
                            })
                    elif method == "skills/list":
                        self._json(200, {
                            "jsonrpc": "2.0", "id": rid,
                            "result": {"skills": card["skills"]}
                        })
                    else:
                        self._json(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}})
                else:
                    self.send_response(404); self.end_headers()
            except Exception as e:
                logging.getLogger(card['name']).error(f"POST error: {e}")
                self._json(500, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}})

        def _json(self, status, data):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, fmt, *args):
            logging.getLogger(card['name']).info(fmt % args)

    return _Handler


def run_server(name: str, desc: str, version: str, port: int, skills: list[dict],
               handler, extra_caps: dict | None = None):
    """Run an A2A agent server. handler(card, text) -> response string"""
    card = make_card(name, desc, version, port, skills, extra_caps)
    server = HTTPServer(("0.0.0.0", port), make_handler(card, handler))
    print(f"[{name}] Agent A2A on http://localhost:{port}/a2a")
    print(f"[{name}] Card at http://localhost:{port}/.well-known/agent-card.json")
    server.serve_forever()
