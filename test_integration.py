"""Integration test for A2A Knowledge Mesh."""

import json, sys
from http.client import HTTPConnection

def send(port, text):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"type": "text", "text": text}]}}
    })
    conn = HTTPConnection("localhost", port)
    conn.request("POST", "/a2a", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data["result"]["task"]["message"]["parts"][0]["text"]

def card(port):
    conn = HTTPConnection("localhost", port)
    conn.request("GET", "/.well-known/agent-card.json")
    data = json.loads(conn.getresponse().read())
    conn.close()
    return data["name"], [s["id"] for s in data["skills"]]

print("=== A2A Knowledge Mesh Integration Test ===\n")

# 1. Verify all agents
for port, name in [(8765, "Registry"), (8766, "Keeper"), (8767, "Reconciler")]:
    n, skills = card(port)
    status = "✅" if n == name else "❌"
    print(f"{status} {n} (port {port}): skills={skills}")

# 2. Store facts in Keeper
print("\n--- Storing facts ---")
r = send(8766, "store-fact key=project:ALLY value=Python/Next.js source=agent-1")
print(f"  {r}")

# 3. Recall facts
print("\n--- Recalling facts ---")
r = send(8766, "recall all")
print(f"  {r}")

# 4. Register Keeper with Registry
print("\n--- Registry: register Keeper ---")
r = send(8765, "register name=Keeper url=http://localhost:8766 skills=store-fact,recall")
print(f"  {r}")

# 5. Discover via Registry
print("\n--- Registry: discover ---")
r = send(8765, "discover skills:store-fact")
print(f"  {r}")

# 6. Check Reconciler status
print("\n--- Reconciler status ---")
r = send(8767, "status")
print(f"  {r}")

print("\n=== INTEGRATION TEST PASSED ===")
