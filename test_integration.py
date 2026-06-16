"""End-to-end integration test — demonstrates full A2A Knowledge Mesh flow.

Uses dynamically generated auth tokens so the test is self-contained.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import json

from agents.auth import a2a_call, configure_auth, sign_body

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"

AGENTS = {
    "registry": {"url": "http://localhost:8765", "proc": None},
    "keeper": {"url": "http://localhost:8766", "proc": None},
    "reconciler": {"url": "http://localhost:8767", "proc": None},
}

# ---------------------------------------------------------------------------
# Auth setup — generate deterministic tokens for the test run
# ---------------------------------------------------------------------------

_TEST_TOKENS = {
    "master": hashlib.sha256(b"test-master-token").hexdigest(),
    "registry": hashlib.sha256(b"test-registry-token").hexdigest(),
    "keeper": hashlib.sha256(b"test-keeper-token").hexdigest(),
    "reconciler": hashlib.sha256(b"test-reconciler-token").hexdigest(),
    "hmac": hashlib.sha256(b"test-hmac-secret").hexdigest(),
}

_PROCS: list[subprocess.Popen] = []


def _setup_env() -> None:
    """Export auth tokens into the environment so subprocesses inherit them."""
    os.environ["A2A_REGISTRY_TOKEN"] = _TEST_TOKENS["registry"]
    os.environ["A2A_KEEPER_TOKEN"] = _TEST_TOKENS["keeper"]
    os.environ["A2A_RECONCILER_TOKEN"] = _TEST_TOKENS["reconciler"]
    os.environ["A2A_MASTER_TOKEN"] = _TEST_TOKENS["master"]
    os.environ["A2A_HMAC_SECRET"] = _TEST_TOKENS["hmac"]


def _configure_local_auth() -> None:
    """Configure auth tokens in-process for direct a2a_call usage."""
    configure_auth(
        master_token=_TEST_TOKENS["master"],
        registry_token=_TEST_TOKENS["registry"],
        keeper_token=_TEST_TOKENS["keeper"],
        reconciler_token=_TEST_TOKENS["reconciler"],
        hmac_secret=_TEST_TOKENS["hmac"],
    )


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------


def start_agents() -> None:
    """Start all 3 agents as subprocesses with auth tokens inherited."""
    _setup_env()
    _configure_local_auth()

    for name in AGENTS:
        env = os.environ.copy()
        p = subprocess.Popen(
            [sys.executable, "-m", f"agents.{name}"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        AGENTS[name]["proc"] = p
        _PROCS.append(p)
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


# ---------------------------------------------------------------------------
# Test flow
# ---------------------------------------------------------------------------


async def test_flow() -> int:
    """Run the full A2A Knowledge Mesh demo flow (all RPCs are authenticated)."""
    url_reg = AGENTS["registry"]["url"]
    url_keeper = AGENTS["keeper"]["url"]
    url_reconciler = AGENTS["reconciler"]["url"]

    print("\n=== Step 1: Register Keeper with Registry ===")
    result = await a2a_call(
        url_reg, "register",
        {
            "agent_id": "keeper",
            "name": "Keeper Agent",
            "card_url": f"{url_keeper}/.well-known/agent-card.json",
            "skills": ["store-fact", "recall", "list-facts", "detect-conflicts"],
            "url": url_keeper,
        },
        target_role="registry",
    )
    assert result.get("status") == "registered", f"register failed: {result}"
    print("  ✓ keeper registered")

    print("\n=== Step 2: Discover keeper by skill ===")
    result = await a2a_call(url_reg, "discover", {"skill": "store-fact"}, target_role="registry")
    agents_result = result.get("agents", [])
    assert len(agents_result) >= 1, f"expected at least 1 agent, got {agents_result}"
    print(f"  ✓ found {len(agents_result)} agent(s) with 'store-fact' skill")

    print("\n=== Step 3: Store facts from source A ===")
    result = await a2a_call(
        url_keeper, "store-fact",
        {
            "subject": "project-ALLY",
            "predicate": "framework",
            "object": "Next.js",
            "source_id": "docs-repo",
            "source_url": "https://github.com/org/ally/docs",
        },
        target_role="keeper",
    )
    assert "id" in result, f"store-fact failed: {result}"
    print(f"  ✓ fact stored (id={result['id']}) — source: docs-repo")

    result = await a2a_call(
        url_keeper, "store-fact",
        {
            "subject": "project-ALLY",
            "predicate": "framework",
            "object": "FastAPI",
            "source_id": "code-repo",
            "source_url": "https://github.com/org/ally/pyproject.toml",
        },
        target_role="keeper",
    )
    assert "id" in result, f"store-fact failed: {result}"
    print(f"  ✓ fact stored (id={result['id']}) — source: code-repo")

    print("\n=== Step 4: Recall facts for project-ALLY ===")
    result = await a2a_call(url_keeper, "recall", {"subject": "project-ALLY"}, target_role="keeper")
    facts = result.get("facts", [])
    assert len(facts) >= 2, f"expected ≥2 facts, got {len(facts)}"
    print(f"  ✓ recalled {len(facts)} fact(s)")
    for f in facts:
        print(f"    [{f['source_id']}] {f['subject']} → {f['predicate']} = {f['object']}")

    print("\n=== Step 5: Detect conflicts (SQL JOIN) ===")
    result = await a2a_call(url_reconciler, "detect-conflict", {}, target_role="reconciler")
    conflicts = result.get("conflicts", [])
    print(f"  ✓ detected {len(conflicts)} conflict(s)")
    for c in conflicts:
        print(f"    conflict {c['conflict_id']}: {c['subject']} ({c['predicate']})")
        if c.get("ai_suggested_fact_id"):
            print(f"      💡 AI suggests fact {c['ai_suggested_fact_id']}: {c.get('ai_reason', '')}")

    if conflicts:
        print("\n=== Step 6: Resolve a conflict ===")
        c = conflicts[0]
        result = await a2a_call(
            url_reconciler, "resolve",
            {
                "conflict_id": c["conflict_id"],
                "resolution_fact_id": 2,  # code-repo wins
                "reason": "code-repo is the source of truth (pyproject.toml is authoritative)",
            },
            target_role="reconciler",
        )
        assert result.get("status") == "resolved", f"resolve failed: {result}"
        print(f"  ✓ resolved: {result}")

    print("\n=== Step 7: Check status ===")
    result = await a2a_call(url_reconciler, "status", {}, target_role="reconciler")
    open_count = len(result.get("open", []))
    all_count = len(result.get("all", []))
    print(f"  ✓ {open_count} open / {all_count} total conflicts")
    all_conflicts = result.get("all", [])
    if all_conflicts:
        c = all_conflicts[0]
        print(f"    - AI suggestion for {c['conflict_id']}: "
              f"Suggested Fact ID {c.get('ai_suggested_fact_id')} - "
              f"Reason: {c.get('ai_reason')}")

    print("\n=== Step 8: Test JSON-RPC error compliance ===")
    # Missing auth
    bad_payload = {
        "jsonrpc": "2.0", "id": "test-err", "method": "list-facts", "params": {}
    }
    resp = httpx.post(f"{url_keeper}/a2a", json=bad_payload, timeout=5)
    assert resp.status_code in (401, 403), f"expected 401/403 for no auth, got {resp.status_code}"
    error_data = resp.json()
    assert "error" in error_data, f"expected error object: {error_data}"
    print(f"  ✓ no-auth request correctly returns HTTP {resp.status_code}")

    # Invalid jsonrpc version
    bad_version = {
        "jsonrpc": "1.0", "id": "test-ver", "method": "list-facts", "params": {}
    }
    bad_version_bytes = json.dumps(bad_version).encode("utf-8")
    resp = httpx.post(
        f"{url_keeper}/a2a",
        content=bad_version_bytes,
        timeout=5,
        headers={
            "Authorization": f"Bearer {_TEST_TOKENS['keeper']}",
            "X-A2A-Signature": sign_body(bad_version_bytes),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}"
    error_data = resp.json()
    assert error_data.get("error", {}).get("code") == -32600, f"expected -32600: {error_data}"
    print("  ✓ wrong jsonrpc version returns -32600")

    # Unknown method
    bad_method = {"jsonrpc": "2.0", "id": "test-unk", "method": "nonexistent", "params": {}}
    bad_method_bytes = json.dumps(bad_method).encode("utf-8")
    resp = httpx.post(
        f"{url_keeper}/a2a",
        content=bad_method_bytes,
        timeout=5,
        headers={
            "Authorization": f"Bearer {_TEST_TOKENS['keeper']}",
            "X-A2A-Signature": sign_body(bad_method_bytes),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}"
    error_data = resp.json()
    assert error_data.get("error", {}).get("code") == -32601, f"expected -32601: {error_data}"
    print("  ✓ unknown method returns -32601")

    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("A2A Knowledge Mesh — Integration Test")
    print("─" * 40)

    # Clean previous run data BEFORE starting agents
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)

    exit_code = 1
    try:
        start_agents()
        exit_code = await test_flow()
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        stop_agents()

    if exit_code == 0:
        print("\n✅ All tests passed!")
    else:
        print(f"\n❌ Tests failed with exit code {exit_code}")
    return exit_code


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
