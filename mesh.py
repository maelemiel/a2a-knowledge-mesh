#!/usr/bin/env python3
"""
mesh — CLI for A2A Knowledge Mesh (Band-native).

Sends commands through Band API instead of local HTTP.
Also provides direct DB inspection for debug.

Usage:
  mesh send "store subject=X predicate=Y object=Z source=docs"  → sends to Keeper via Band
  mesh recall <subject>                                           → reads Keeper DB directly
  mesh detect                                                     → reads Keeper DB, detect conflicts
  mesh status                                                     → reads all DBs, show state
  mesh start                                                      → launch all 3 Band agents
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"

BAND_API_KEY = os.getenv("BAND_API_KEY", "")
BAND_BASE_URL = os.getenv("BAND_BASE_URL", "https://app.band.ai")
BAND_AGENT_ID = os.getenv("BAND_AGENT_ID", "")


# ---------------------------------------------------------------------------
# Direct DB inspection (no Band connection needed)
# ---------------------------------------------------------------------------


def _db(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    return sqlite3.connect(str(path))


def cmd_recall(subject: str = "") -> None:
    conn = _db(DATA / "keeper.db")
    if conn is None:
        print("Keeper DB not found. Start agents first.")
        return
    if subject:
        rows = conn.execute(
            "SELECT id, subject, predicate, object, source_id, timestamp "
            "FROM facts WHERE subject=? ORDER BY timestamp DESC", (subject,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, subject, predicate, object, source_id, timestamp "
            "FROM facts ORDER BY timestamp DESC LIMIT 30"
        ).fetchall()
    conn.close()

    if not rows:
        print("No facts.")
        return
    for r in rows:
        print(f"  #{r[0]} [{r[4]}] {r[1]} → {r[2]} = {r[3]}  ({time.ctime(r[5])})")


def cmd_detect() -> None:
    conn = _db(DATA / "keeper.db")
    if conn is None:
        print("Keeper DB not found.")
        return
    rows = conn.execute("""
        SELECT f1.subject, f1.predicate,
               f1.id, f1.object, f1.source_id,
               f2.id, f2.object, f2.source_id
        FROM facts f1
        JOIN facts f2 ON f1.subject = f2.subject
                     AND f1.predicate = f2.predicate
                     AND f1.source_id < f2.source_id
                     AND f1.object != f2.object
    """).fetchall()
    conn.close()

    if not rows:
        print("✅ No conflicts.")
        return
    print(f"⚠️ {len(rows)} conflict(s):")
    for r in rows:
        print(f"  {r[0]} ({r[1]}): #{r[2]} ({r[4]}) vs #{r[5]} ({r[7]})")


def cmd_status() -> None:
    # Keeper DB
    conn = _db(DATA / "keeper.db")
    if conn:
        count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        subjects = conn.execute(
            "SELECT DISTINCT subject FROM facts ORDER BY subject"
        ).fetchall()
        conn.close()
        print(f"📦 Keeper: {count} facts, {len(subjects)} subjects")
        for s in subjects:
            print(f"     • {s[0]}")
    else:
        print("📦 Keeper: not started")

    # Reconciler DB
    conn = _db(DATA / "reconciler.db")
    if conn:
        open_c = conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE status='open'"
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM conflicts"
        ).fetchone()[0]
        conn.close()
        print(f"⚡ Reconciler: {open_c} open / {total} total conflicts")
    else:
        print("⚡ Reconciler: not started")

    # Registry DB
    conn = _db(DATA / "registry.db")
    if conn:
        agents = conn.execute("SELECT name, skills FROM agents").fetchall()
        conn.close()
        print(f"📋 Registry: {len(agents)} registered agent(s)")
        for a in agents:
            try:
                skills = json.loads(a[1])
            except (json.JSONDecodeError, TypeError):
                skills = [a[1]]
            print(f"     • {a[0]} — {', '.join(skills)}")
    else:
        print("📋 Registry: not started")


def cmd_send_via_band(content: str) -> None:
    """Send a command to Keeper via Band REST API."""
    if not BAND_API_KEY or not BAND_AGENT_ID:
        print("⚠️ BAND_API_KEY and BAND_AGENT_ID required to send via Band.")
        print("   Set them in .env or use 'recall'/'detect' for local DB queries.")
        return

    import httpx

    # Find Keeper in contacts/rooms or use predefined room
    room_id = os.getenv("BAND_KEEPER_ROOM_ID", "")
    if not room_id:
        print("⚠️ BAND_KEEPER_ROOM_ID not set. Set the room ID where Keeper listens.")
        return

    payload = {
        "message": {
            "content": content,
        }
    }
    headers = {
        "Authorization": f"Bearer {BAND_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{BAND_BASE_URL}/api/v2/agents/{BAND_AGENT_ID}/rooms/{room_id}/messages"

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=10)
        if resp.is_success:
            print(f"✅ Message sent via Band: {content[:80]}")
        else:
            print(f"❌ Band API error ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Failed to send: {e}")


def cmd_start() -> None:
    """Launch all 3 Band agents as subprocesses."""
    import subprocess

    missing = []
    for var in ["BAND_AGENT_ID", "BAND_API_KEY"]:
        if not os.getenv(var):
            missing.append(var)
    if missing:
        print(f"⚠️ Missing env vars: {', '.join(missing)}")
        print("   Set them in .env before starting agents.")
        return

    procs = []
    for name in ["registry_band", "keeper_band", "reconciler_band"]:
        p = subprocess.Popen(
            [sys.executable, "-m", f"agents.{name}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        procs.append(p)
        print(f"[{name}] started (pid={p.pid})")

    try:
        for p in procs:
            if p.stdout:
                for line in p.stdout:
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()


def cmd_help() -> None:
    print(__doc__)


def main() -> None:
    if len(sys.argv) < 2:
        cmd_help()
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    cmds = {
        "recall": lambda: cmd_recall(args[0] if args else ""),
        "detect": lambda: cmd_detect(),
        "status": lambda: cmd_status(),
        "send": lambda: cmd_send_via_band(" ".join(args)),
        "start": lambda: cmd_start(),
        "help": lambda: cmd_help(),
    }

    f = cmds.get(cmd)
    if not f:
        print(f"Unknown: {cmd}")
        cmd_help()
    else:
        f()


if __name__ == "__main__":
    main()
