"""End-to-end integration test — demonstrates full A2A Knowledge Mesh flow."""

from __future__ import annotations
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"


AGENTS = {
    "registry": {"url": "http://localhost:8765", "proc": None},
    "keeper": {"url": "http://localhost:8766", "proc": None},
    "reconciler": {"url": "http://localhost:8767", "proc": None},
}

ROOT = Path(__file__).parent


def start_agents() -> None:
    """Start all 3 agents as subprocesses."""
    for name in AGENTS:
        p = subprocess.Popen(
            [sys.executable, "-m", f"agents.{name}"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        AGENTS[name]["proc"] = p
        print(f"  ✓ {name} started (pid={p.pid})")

    # Wait for all agents to be ready
    for _ in range(30):
        all_ok = True
        for name, info in AGENTS.items():
            try:
                r = httpx.get(f"{info['url']}/health", timeout=2)
                all_ok = all_ok and r.status_code == 200
            except Exception:
                all_ok = False
        if all_ok:
            print("  ✓ all agents healthy")
            return
        time.sleep(0.5)

    print("  ✗ agents did not become healthy in time")
    stop_agents()
    sys.exit(1)


def stop_agents() -> None:
    for name, info in AGENTS.items():
        if info["proc"]:
            info["proc"].terminate()
            try:
                info["proc"].wait(timeout=3)
            except subprocess.TimeoutExpired:
                info["proc"].kill()
    print("  ✓ agents stopped")


def a2a_call(url: str, method: str, params: dict | None = None) -> dict:
    """Make an A2A RPC call to an agent."""
    payload = {
        "jsonrpc": "2.0",
        "id": f"test-{method}",
        "method": method,
        "params": params or {},
    }
    resp = httpx.post(f"{url}/a2a", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"A2A error: {data['error']}")
    return data.get("result", {})


def test_flow() -> int:
    """Run the full A2A Knowledge Mesh demo flow."""
    print("\n=== Step 1: Register Keeper with Registry ===")
    a2a_call(
        AGENTS["registry"]["url"],
        "register",
        {
            "agent_id": "keeper-1",
            "name": "Keeper Agent",
            "card_url": "http://localhost:8766/.well-known/agent-card.json",
            "skills": ["store-fact", "recall", "list-facts"],
            "url": "http://localhost:8766",
        },
    )
    print("  ✓ keeper registered")

    print("\n=== Step 2: Discover keeper by skill ===")
    result = a2a_call(AGENTS["registry"]["url"], "discover", {"skill": "store-fact"})
    agents = result.get("agents", [])
    assert len(agents) >= 1, f"expected at least 1 agent, got {agents}"
    print(f"  ✓ found {len(agents)} agent(s) with 'store-fact' skill")

    print("\n=== Step 3: Store facts from source A ===")
    a2a_call(
        AGENTS["keeper"]["url"],
        "store-fact",
        {
            "subject": "project-ALLY",
            "predicate": "framework",
            "object": "Next.js",
            "source_id": "docs-repo",
            "source_url": "https://github.com/org/ally/docs",
        },
    )
    print("  ✓ fact stored (source: docs-repo)")

    a2a_call(
        AGENTS["keeper"]["url"],
        "store-fact",
        {
            "subject": "project-ALLY",
            "predicate": "framework",
            "object": "FastAPI",
            "source_id": "code-repo",
            "source_url": "https://github.com/org/ally/pyproject.toml",
        },
    )
    print("  ✓ fact stored (source: code-repo)")

    print("\n=== Step 4: Recall facts for project-ALLY ===")
    result = a2a_call(AGENTS["keeper"]["url"], "recall", {"subject": "project-ALLY"})
    facts = result.get("facts", [])
    print(f"  ✓ recalled {len(facts)} fact(s)")
    for f in facts:
        print(f"    [{f['source_id']}] {f['subject']} → {f['predicate']} = {f['object']}")

    print("\n=== Step 5: Detect conflicts ===")
    result = a2a_call(AGENTS["reconciler"]["url"], "detect-conflict", {})
    conflicts = result.get("conflicts", [])
    print(f"  ✓ detected {len(conflicts)} conflict(s)")
    for c in conflicts:
        print(f"    conflict {c['conflict_id']}: {c['subject']} ({c['predicate']})")

    if conflicts:
        print("\n=== Step 6: Resolve a conflict ===")
        c = conflicts[0]
        result = a2a_call(
            AGENTS["reconciler"]["url"],
            "resolve",
            {
                "conflict_id": c["conflict_id"],
                "resolution_fact_id": 2,  # code-repo wins
                "reason": "code-repo is the source of truth (pyproject.toml is authoritative)",
            },
        )
        print(f"  ✓ resolved: {result}")

    print("\n=== Step 7: Check status ===")
    result = a2a_call(AGENTS["reconciler"]["url"], "status", {})
    open_count = len(result.get("open", []))
    all_count = len(result.get("all", []))
    print(f"  ✓ {open_count} open / {all_count} total conflicts")

    return 0


def main() -> None:
    print("A2A Knowledge Mesh — Integration Test")
    print("─" * 40)

    # Clean previous run data BEFORE starting agents
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)

    try:
        start_agents()
        exit_code = test_flow()
    finally:
        stop_agents()

    if exit_code == 0:
        print("\n✅ All tests passed!")
    else:
        print(f"\n❌ Tests failed with exit code {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
