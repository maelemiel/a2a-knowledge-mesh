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

import json
import sys
import time
from http.client import HTTPConnection

DEFAULT_PORTS = {"registry": 8765, "keeper": 8766, "reconciler": 8767}


def _a2a(port: int, method: str, params: dict | None = None) -> dict:
    """Make a JSON-RPC 2.0 call to an agent."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    })
    conn = HTTPConnection("localhost", port, timeout=5)
    conn.request("POST", "/a2a", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    if "error" in data:
        return {"error": data["error"]}
    return data.get("result", {})


def _parse_store(raw: str) -> dict:
    """Parse 'subject=ALLY predicate=framework object=Next.js source=docs' into params."""
    params: dict[str, str] = {}
    source = "cli"
    for part in raw.split():
        if "=" in part:
            k, v = part.split("=", 1)
            if k == "source":
                source = v
            else:
                params[k] = v
    params.setdefault("source_id", source)
    return params


def cmd_store(raw: str) -> None:
    params = _parse_store(raw)
    result = _a2a(DEFAULT_PORTS["keeper"], "store-fact", params)
    print(json.dumps(result, indent=2))


def cmd_recall(query: str = "all") -> None:
    params: dict[str, str] = {}
    if query != "all":
        params["subject"] = query
    result = _a2a(DEFAULT_PORTS["keeper"], "recall", params)
    facts = result.get("facts", [])
    for f in facts:
        print(f"  [{f['source_id']}] {f['subject']} → {f['predicate']} = {f['object']}")


def cmd_discover(keyword: str = "") -> None:
    params: dict[str, str] = {}
    if keyword:
        params["skill"] = keyword
    result = _a2a(DEFAULT_PORTS["registry"], "discover", params)
    print(json.dumps(result, indent=2))


def cmd_detect() -> None:
    result = _a2a(DEFAULT_PORTS["reconciler"], "detect-conflict")
    conflicts = result.get("conflicts", [])
    print(f"Conflicts: {len(conflicts)}")
    for c in conflicts:
        print(f"  {c.get('conflict_id')}: {c.get('subject')} ({c.get('predicate')})")


def cmd_resolve(conflict_id: str, resolution_fact_id: str) -> None:
    if not conflict_id or not resolution_fact_id:
        print("Usage: mesh resolve <conflict_id> <resolution_fact_id>")
        return
    result = _a2a(DEFAULT_PORTS["reconciler"], "resolve", {
        "conflict_id": conflict_id,
        "resolution_fact_id": int(resolution_fact_id),
        "reason": "resolved via CLI",
    })
    print(json.dumps(result, indent=2))


def cmd_status() -> None:
    for name, port in DEFAULT_PORTS.items():
        try:
            conn = HTTPConnection("localhost", port, timeout=2)
            conn.request("GET", "/.well-known/agent-card.json")
            card = json.loads(conn.getresponse().read())
            conn.close()
            skills = card.get("skills", [])
            print(f"  ✅ {name} (port {port}) — {', '.join(skills)}")
        except Exception as e:
            print(f"  ❌ {name} (port {port}) — {e}")


def cmd_start() -> None:
    from agents.runner import run_all
    run_all()
    print("Agents started. Use 'mesh status' to verify.")


def cmd_watch(interval: str = "10") -> None:
    sec = int(interval)
    print(f"Watching for conflicts every {sec}s. Ctrl+C to stop.", flush=True)
    try:
        while True:
            result = _a2a(DEFAULT_PORTS["reconciler"], "detect-conflict")
            ts = time.strftime("%H:%M:%S")
            conflicts = result.get("conflicts", [])
            print(f"[{ts}] {len(conflicts)} conflict(s)", flush=True)
            time.sleep(sec)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_help() -> None:
    print(__doc__)


def main() -> None:
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    cmds: dict[str, callable] = {
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
        print(f"Unknown: {cmd}")
        cmd_help()
        sys.exit(1)
    f()


if __name__ == "__main__":
    main()
