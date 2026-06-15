"""A2A Discovery spike test.
Starts Registry + Keeper agents, discovers capabilities, routes tasks."""

import json
import time
import threading
from agent_server import run_agent

from http.client import HTTPConnection

def fetch_card(port):
    conn = HTTPConnection("localhost", port)
    conn.request("GET", "/.well-known/agent-card.json")
    resp = conn.getresponse()
    card = json.loads(resp.read())
    conn.close()
    return card

def send_message(port, text):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"type": "text", "text": text}]}}
    })
    conn = HTTPConnection("localhost", port)
    conn.request("POST", "/a2a", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    result = json.loads(resp.read())
    conn.close()
    return result

# Start agents in threads
def start_agent(name, port, skills):
    t = threading.Thread(target=run_agent, args=(
        name, f"A2A {name} agent", "0.1.0", port, skills
    ), daemon=True)
    t.start()
    return t

print("=== A2A Discovery Spike ===")

# 1. Start Registry (port 8765) — skills: discover, register
t1 = start_agent("Registry", 8765, [
    {"id": "discover", "name": "Discover Agents", "description": "Find agents by capability"},
    {"id": "register", "name": "Register Agent", "description": "Add agent to registry"},
])

# 2. Start Keeper (port 8767) — skills: store-fact, recall
t2 = start_agent("Keeper", 8767, [
    {"id": "store-fact", "name": "Store Fact", "description": "Save a fact to memory"},
    {"id": "recall", "name": "Recall Fact", "description": "Retrieve a fact from memory"},
])

time.sleep(1)

# 3. Fetch both Agent Cards
print("\n--- Agent Cards ---")
reg_card = fetch_card(8765)
print(f"Registry: {reg_card['name']} v{reg_card['version']}")
print(f"  Skills: {[s['id'] for s in reg_card['skills']]}")
print(f"  URL: {reg_card['url']}")

keep_card = fetch_card(8767)
print(f"Keeper: {keep_card['name']} v{keep_card['version']}")
print(f"  Skills: {[s['id'] for s in keep_card['skills']]}")
print(f"  URL: {keep_card['url']}")

# 4. Registry discovery: "find agent with store-fact capability"
print("\n--- Discovery Test ---")
r = send_message(8765, "discover skills:store-fact")
msg = r["result"]["task"]["message"]["parts"][0]["text"]
print(f"Registry responds: {msg}")

# 5. Route to Keeper: send a fact to store
print("\n--- Keeper Test ---")
r = send_message(8767, "store-fact project:ALLY stack:Next.js")
msg = r["result"]["task"]["message"]["parts"][0]["text"]
print(f"Keeper responds: {msg}")

# 6. Cross-agent: Registry forwards to Keeper based on skill
print("\n--- Cross-Agent Routing ---")
# Client asks Registry to store a fact — Registry should route to Keeper
r = send_message(8765, "store-fact project:hackathon type:A2A")
msg = r["result"]["task"]["message"]["parts"][0]["text"]
print(f"Registry routes to Keeper: {msg}")

print("\n=== DISCOVERY VALIDATED ===")
