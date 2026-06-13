"""Registry Agent.
Maintains agent directory. Skills: discover, register."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.a2a_server import run_server

SKILLS = [
    {"id": "discover", "name": "Discover Agents",
     "description": "Find agents by capability. Usage: discover skills:store-fact"},
    {"id": "register", "name": "Register Agent",
     "description": "Add agent to directory. Usage: register name=X url=Y skills=A,B"},
]

KNOWN_AGENTS: dict[str, dict] = {}

def handle(card, text):
    words = text.lower().split()
    if text.startswith("discover"):
        query = text[len("discover"):].strip().replace("skills:", "").strip()
        results = [f"{v['name']} at {v['url']} (skill: {k})" for k, v in KNOWN_AGENTS.items() if query in k]
        if results:
            return f"Registry found: {'; '.join(results)}"
        return f"No agent for '{query}'. Known: {list(KNOWN_AGENTS.keys()) or 'none'}"
    elif text.startswith("register"):
        parts = text[len("register"):].strip().split()
        name, url, skills = "", "", []
        for p in parts:
            if p.startswith("name="): name = p[5:]
            elif p.startswith("url="): url = p[4:]
            elif p.startswith("skills="): skills = p[7:].split(",")
        if name and url:
            for s in skills: KNOWN_AGENTS[s] = {"name": name, "url": url}
            return f"Registered '{name}' with skills {skills}"
        return "Usage: register name=X url=Y skills=A,B"
    return f"Registry ready. Skills: {[s['id'] for s in SKILLS]}. Agents: {len(KNOWN_AGENTS)}"

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run_server("Registry", "A2A agent directory — discover and register agents",
               "1.0.0", port, SKILLS, handle)

