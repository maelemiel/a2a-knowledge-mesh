#!/usr/bin/env python3
"""
mesh — Unified CLI for A2A Knowledge Mesh.

Usage:
  mesh start                       Start all 3 agents (background)
  mesh store <key>=<value>         Store a fact in Keeper
  mesh recall [key|all]            Recall stored facts
  mesh discover [keyword]          Discover agents by skill
  mesh detect                      Scan for conflicts
  mesh resolve <key> <store>       Resolve a conflict
  mesh status                      Show system health
  mesh watch [interval_sec]        Auto-scan conflicts every N seconds
  mesh help                        Show this message
"""

import sys, os, json, time, threading
from http.client import HTTPConnection

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_PORTS = {"registry": 8765, "keeper": 8766, "reconciler": 8767}

def _a2a(port: int, text: str) -> str:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"type": "text", "text": text}]}}
    })
    conn = HTTPConnection("localhost", port, timeout=5)
    conn.request("POST", "/a2a", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    try:
        return data["result"]["task"]["message"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return f"Error: {json.dumps(data.get('error', data), indent=2)}"


def _parse_store(raw: str) -> str:
    """Convert user-friendly store format to agent syntax.
    'project:ALLY=Python store=live source=github'
    → 'store-fact key=project:ALLY value=Python store=live source=github'"""
    parts = raw.split()
    formatted = []
    for p in parts:
        if any(p.startswith(prefix) for prefix in ("key=", "value=", "source=", "store=")):
            formatted.append(p)
        elif p.startswith("store=") or p.startswith("source="):
            formatted.append(p)
        elif "=" in p:
            k, v = p.split("=", 1)
            formatted.append(f"key={k}")
            formatted.append(f"value={v}")
        else:
            formatted.append(p)
    return f"store-fact {' '.join(formatted)}"


def cmd_store(raw: str):
    text = _parse_store(raw)
    print(_a2a(DEFAULT_PORTS["keeper"], text))

def cmd_recall(query: str = "all"):
    print(_a2a(DEFAULT_PORTS["keeper"], f"recall {query}"))

def cmd_discover(keyword: str = ""):
    q = f"discover skills:{keyword}" if keyword else "discover skills:"
    print(_a2a(DEFAULT_PORTS["registry"], q))

def cmd_detect():
    print(_a2a(DEFAULT_PORTS["reconciler"], "detect-conflict"))

def cmd_resolve(key: str, store: str):
    if not key or not store:
        return print("Usage: mesh resolve <key> <winning_store>")
    print(_a2a(DEFAULT_PORTS["reconciler"], f"resolve key={key} winning_store={store}"))

def cmd_status():
    for name, port in DEFAULT_PORTS.items():
        try:
            conn = HTTPConnection("localhost", port, timeout=2)
            conn.request("GET", "/.well-known/agent-card.json")
            card = json.loads(conn.getresponse().read())
            conn.close()
            skills = [s["id"] for s in card.get("skills", [])]
            print(f"  ✅ {name} (port {port}) — {', '.join(skills)}")
        except Exception as e:
            print(f"  ❌ {name} (port {port}) — {e}")

def cmd_start():
    from agents.runner import run_all
    run_all()
    print("Agents started. Use 'mesh status' to verify.")

def cmd_watch(interval: str = "10"):
    sec = int(interval)
    print(f"Watching for conflicts every {sec}s. Ctrl+C to stop.", flush=True)
    try:
        while True:
            result = _a2a(DEFAULT_PORTS["reconciler"], "detect-conflict")
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {result}", flush=True)
            time.sleep(sec)
    except KeyboardInterrupt:
        print("\nStopped.")

def cmd_help():
    print(__doc__)

def main():
    if len(sys.argv) < 2:
        cmd_help(); sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    cmds = {
        "start": lambda: cmd_start(),
        "store": lambda: cmd_store(" ".join(args)),
        "recall": lambda: cmd_recall(args[0] if args else "all"),
        "discover": lambda: cmd_discover(args[0] if args else ""),
        "detect": lambda: cmd_detect(),
        "resolve": lambda: cmd_resolve(args[0] if len(args) > 0 else "", args[1] if len(args) > 1 else ""),
        "status": lambda: cmd_status(),
        "watch": lambda: cmd_watch(args[0] if args else "10"),
        "help": lambda: cmd_help(),
    }
    f = cmds.get(cmd)
    if not f:
        print(f"Unknown: {cmd}"); cmd_help(); sys.exit(1)
    f()

if __name__ == "__main__":
    main()
