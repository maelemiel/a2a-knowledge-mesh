"""Reconciler Agent.
Detects conflicts across fact stores, creates Band rooms for resolution.
Skills: detect-conflict, resolve, status."""

import sys, os, tempfile, json
from http.client import HTTPConnection
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.a2a_server import run_server
from lib.conflict import init_db, upsert, get_facts, detect_conflicts, resolve_conflict

SKILLS = [
    {"id": "detect-conflict", "name": "Detect Conflicts",
     "description": "Scan Keeper for conflicts. Usage: detect-conflict"},
    {"id": "resolve", "name": "Resolve Conflict",
     "description": "Resolve a conflict. Usage: resolve key=X winning_store=Y"},
    {"id": "status", "name": "System Status",
     "description": "Show overall system health"},
]

DB_PATH = os.environ.get("RECONCILER_DB", os.path.join(tempfile.gettempdir(), "reconciler_facts.db"))
conn = init_db(DB_PATH)

# Band credentials (loaded from env)
BAND_API_KEY = os.environ.get("BAND_API_KEY", "")
REGISTRY_URL = "http://localhost:8765"
KEEPER_URL = "http://localhost:8766"
BAND_HUMAN_ID = os.environ.get("BAND_HUMAN_ID", "")


def _a2a_request(url: str, text: str) -> dict:
    """Send an A2A message to another agent."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"type": "text", "text": text}]}}
    })
    host, port_part = url.replace("http://", "").split(":")
    conn = HTTPConnection(host, int(port_part))
    conn.request("POST", "/a2a", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data


def _create_band_room(title: str, content: str, mention_ids: list[str]) -> str | None:
    """Create a Band room and send a message. Returns room_id or None."""
    if not BAND_API_KEY:
        return None
    import httpx
    try:
        with httpx.Client(base_url="https://app.band.ai", headers={"X-API-Key": BAND_API_KEY, "Content-Type": "application/json"}) as c:
            r = c.post("/api/v1/agent/chats", json={"chat": {"title": title}})
            room_id = r.json()["data"]["id"]
            if BAND_HUMAN_ID:
                c.post(f"/api/v1/agent/chats/{room_id}/participants", json={"participant": {"participant_id": BAND_HUMAN_ID, "role": "member"}})
            c.post(f"/api/v1/agent/chats/{room_id}/messages", json={
                "message": {"content": content, "mentions": [{"id": uid} for uid in mention_ids]}
            })
            return room_id
    except Exception as e:
        print(f"[Reconciler] Band error: {e}")
        return None


def handle(card, text):
    if text.startswith("detect-conflict"):
        # Fetch facts from Keeper
        try:
            resp = _a2a_request(KEEPER_URL, "recall all")
            keeper_msg = resp.get("result", {}).get("task", {}).get("message", {}).get("parts", [{}])[0].get("text", "")
        except Exception as e:
            return f"Error contacting Keeper: {e}"

        # Parse facts from Keeper response
        # If Keeper has data, load it into our DB for conflict detection
        conflicts = detect_conflicts(conn)

        if not conflicts:
            return f"No conflicts detected. Keeper: {keeper_msg[:100]}"

        # Report conflicts and create Band room for first one
        first = conflicts[0]
        msg = (
            f"Conflicts detected: {len(conflicts)}\n"
            f"  1. {first['key']}: [{first['store_a']}]={first['value_a']} vs [{first['store_b']}]={first['value_b']}"
        )

        room_id = _create_band_room(
            f"conflict-{first['key'].replace(':', '-')}",
            f"CONFLICT: {first['key']} has 2 values\n"
            f"  [{first['store_a']}] says: {first['value_a']}\n"
            f"  [{first['store_b']}] says: {first['value_b']}\n"
            f"Please resolve.",
            [BAND_HUMAN_ID] if BAND_HUMAN_ID else []
        )
        if room_id:
            msg += f"\nBand room: {room_id}"
        return msg

    elif text.startswith("resolve"):
        parts = text[len("resolve"):].strip().split()
        key, store = "", ""
        for p in parts:
            if p.startswith("key="): key = p[4:]
            elif p.startswith("winning_store="): store = p[14:]
        if key and store:
            try:
                resolve_conflict(conn, key, store)
                return f"Resolved: {key} -> {store} (all stores updated)"
            except ValueError as e:
                return f"Error: {e}"
        return "Usage: resolve key=X winning_store=Y"

    elif text.startswith("status"):
        conflicts = detect_conflicts(conn)
        facts = get_facts(conn)
        return (
            f"Reconciler status:\n"
            f"  Facts tracked: {len(facts)}\n"
            f"  Conflicts: {len(conflicts)}\n"
            f"  Band API: {'configured' if BAND_API_KEY else 'not configured'}\n"
            f"  Keeper: {KEEPER_URL}\n"
            f"  Registry: {REGISTRY_URL}"
        )

    return (f"Reconciler ready. Skills: {[s['id'] for s in SKILLS]}. "
            f"Conflicts: {len(detect_conflicts(conn))}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
    run_server("Reconciler", "A2A conflict resolver — detect and resolve fact contradictions",
               "1.0.0", port, SKILLS, handle)
