"""Keeper Agent.
Stores and retrieves facts (SQLite). Skills: store-fact, recall."""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.a2a_server import run_server
from lib.conflict import init_db, upsert, get_facts

SKILLS = [
    {"id": "store-fact", "name": "Store Fact",
     "description": "Save a fact. Usage: store-fact key=value source=X"},
    {"id": "recall", "name": "Recall Fact",
     "description": "Retrieve facts. Usage: recall key=X or recall all"},
]

DB_PATH = os.environ.get("KEEPER_DB", os.path.join(tempfile.gettempdir(), "keeper_facts.db"))
conn = init_db(DB_PATH)

def handle(card, text):
    if text.startswith("store-fact"):
        parts = text[len("store-fact"):].strip().split()
        key, value, source, store = "", "", "manual", "live"
        for p in parts:
            if p.startswith("key="): key = p[4:]
            elif p.startswith("value="): value = p[6:]
            elif p.startswith("source="): source = p[7:]
            elif p.startswith("store="): store = p[6:]
        if key and value:
            upsert(conn, store, key, value, source)
            return f"Stored: {key} = {value} (store: {store}, source: {source})"
        return "Usage: store-fact key=X value=Y [source=Z] [store=NAME]"

    elif text.startswith("recall"):
        query = text[len("recall"):].strip().replace("key=", "").strip()
        facts = get_facts(conn)
        if query == "all" or not query:
            if not facts:
                return "No facts stored."
            return "Facts:\n" + "\n".join(f"  [{s}] {k} = {v}" for s, k, v, _ in facts)
        matches = [f"  [{s}] {k} = {v} (source: {src})" for s, k, v, src in facts if query in k]
        if matches:
            return "Matches:\n" + "\n".join(matches)
        return f"No fact found for '{query}'"

    return f"Keeper ready. Skills: {[s['id'] for s in SKILLS]}. Facts: {len(get_facts(conn))}"

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    run_server("Keeper", "A2A fact storage — store and recall structured facts",
               "1.0.0", port, SKILLS, handle)
