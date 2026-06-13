"""A2A Agent Card server spike.
Serves /.well-known/agent-card.json + handles JSON-RPC 2.0."""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler

AGENT_CARD = {
    "name": "Registry Spike",
    "description": "A2A agent that discovers other agents and maintains knowledge network",
    "version": "0.1.0",
    "url": "http://localhost:8765/a2a",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False,
    },
    "skills": [
        {
            "id": "discover",
            "name": "Discover Agents",
            "description": "Find agents with specific capabilities",
        },
        {
            "id": "register",
            "name": "Register Agent",
            "description": "Add an agent to the registry",
        },
    ],
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "securitySchemes": [],
}

class A2AHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/.well-known/agent-card.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(AGENT_CARD).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/a2a":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                req = json.loads(body)
                method = req.get("method")
                req_id = req.get("id")

                if method == "message/send":
                    parts = req.get("params", {}).get("message", {}).get("parts", [])
                    text = parts[0].get("text", "") if parts else ""
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "task": {
                                "id": f"task-{req_id}",
                                "status": "completed",
                                "message": {
                                    "role": "agent",
                                    "parts": [{
                                        "type": "text",
                                        "text": f"Registry a recu: '{text}'. Skills dispo: discover, register"
                                    }]
                                }
                            }
                        }
                    }
                else:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": "Method not found"}
                    }
            except Exception as e:
                resp = {"jsonrpc": "2.0", "id": req.get("id", None), "error": {"code": -32700, "message": str(e)}}

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[A2A] {fmt % args}")

if __name__ == "__main__":
    port = 8765
    server = HTTPServer(("0.0.0.0", port), A2AHandler)
    print(f"A2A Agent Card server on http://localhost:{port}")
    print(f"Card: http://localhost:{port}/.well-known/agent-card.json")
    print(f"JSON-RPC: POST http://localhost:{port}/a2a")
    server.serve_forever()
