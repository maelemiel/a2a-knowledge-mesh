"""A2A agent server — configurable by name/port/skills."""
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

def make_agent_card(name, desc, version, port, skills, extra_caps=None):
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

def make_handler(card):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/.well-known/agent-card.json":
                self._json(200, card)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/a2a":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                req, rid = body, body.get("id")
                method = req.get("method")

                if method == "message/send":
                    parts = req.get("params",{}).get("message",{}).get("parts",[])
                    text = parts[0].get("text","") if parts else ""
                    result = handle_message(card, text, rid)
                    self._json(200, {"jsonrpc":"2.0","id":rid,"result":{"task":{"id":f"t-{rid}","status":"completed","message":{"role":"agent","parts":[{"type":"text","text":result}]}}}})
                else:
                    self._json(200, {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":"Method not found"}})
            else:
                self.send_response(404)
                self.end_headers()

        def _json(self, status, data):
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, fmt, *args):
            print(f"[{card['name']}] {fmt % args}")
    return Handler

def handle_message(card, text, req_id):
    skill_id = None
    for word in text.lower().split():
        for s in card["skills"]:
            if word == s["id"]:
                skill_id = s["id"]
                break
    if skill_id:
        return f"{card['name']} executing '{skill_id}': {text}"
    return f"{card['name']} received: '{text}'. Skills: {[s['id'] for s in card['skills']]}"

def run_agent(name, desc, version, port, skills, extra_caps=None):
    card = make_agent_card(name, desc, version, port, skills, extra_caps)
    server = HTTPServer(("0.0.0.0", port), make_handler(card))
    print(f"[{name}] on http://localhost:{port}")
    print(f"[{name}] Card: http://localhost:{port}/.well-known/agent-card.json")
    server.serve_forever()

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Agent"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    run_agent(name, f"A2A {name} agent", "0.1.0", port, [])
