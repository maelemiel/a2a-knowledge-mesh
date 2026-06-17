#!/usr/bin/env python3
"""Offline demo — reproduces the full Knowledge Mesh workflow without Band or LLM.

Usage:
  uv run python scripts/run_offline_demo.py [fixture file]

If no fixture is given, runs all 4 scenarios sequentially.

Outputs the same messages the agents would post in a Band room,
and pushes events to the bridge buffer so the dashboard updates.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# Ensure we can import from the project
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agents.keeper import KeeperStore
from agents.reconciler import ReconcilerStore, _llm_suggest, _build_conflict_message

logging.basicConfig(level=logging.WARNING)

# ── Push events to bridge buffer (so dashboard sees them) ─────────────

try:
    from agents.bridge_agent import push_event, Event, clear_events
    _HAS_BRIDGE = True
except ImportError:
    _HAS_BRIDGE = False

    class Event:
        def __init__(self, *args, **kwargs):
            pass

    def push_event(*args, **kwargs):
        pass

    def clear_events():
        pass


def println(tag: str, message: str, color: str = "") -> None:
    """Print a line as if it came from a Band agent."""
    if color:
        print(f"{color}[{tag}]{' ' * (2 if len(tag) < 11 else 1)}{message}\033[0m")
    else:
        print(f"[{tag}]  {message}")
    push_event(Event("message", message, sender_name=tag))


def simulate_run(keeper: KeeperStore, reconciler: ReconcilerStore, facts: list[dict],
                 scenario: str, narration: str) -> dict:
    """Simulate the Scraper → Keeper → Reconciler workflow."""
    print("")
    println("DEMO", f"═══ {scenario} ═══", "\033[1;36m")
    println("DEMO", narration, "\033[2;37m")
    time.sleep(0.5)

    # Step 1: Scraper sends facts
    println("Scraper", f"🔍 Scanning repository... {len(facts)} fact(s) found", "\033[1;34m")
    time.sleep(0.3)

    # Step 2: Keeper stores facts
    stored = keeper.store_batch(facts)
    println("Keeper", f"✅ Stored {len(stored)} fact(s)", "\033[1;32m")
    time.sleep(0.3)

    # Step 3: Keeper detects conflicts
    conflicts = keeper.detect_conflicts()
    if not conflicts:
        println("Keeper", "✅ No conflicts found — all facts are consistent.", "\033[1;32m")
        return {"status": "clean", "scenario": scenario}

    println("Keeper", f"⚠️ {len(conflicts)} conflict(s) detected!", "\033[1;33m")
    for c in conflicts:
        println("Keeper",
                f"  `{c['subject']}` → {c['predicate']}: "
                f"`{c['object_a']}` (src:{c['source_a']}) vs "
                f"`{c['object_b']}` (src:{c['source_b']})",
                "\033[1;33m")
    time.sleep(0.5)

    # Step 4: Reconciler analyzes (without LLM in offline mode)
    result_text = ""
    for c in conflicts:
        fa = keeper.get_fact(c["fact_a_id"])
        fb = keeper.get_fact(c["fact_b_id"])
        if fa and fb:
            # Use timestamp-based resolution (no LLM in offline mode)
            winner = fa if fa["timestamp"] >= fb["timestamp"] else fb
            result_text = (
                f"Fact #{winner['id']} gagnant — "
                f"{winner['source_id']} (timestamp le plus récent)"
            )
            println("Reconciler", f"💡 {result_text}", "\033[1;35m")

            conflict = reconciler.create_conflict(
                subject=c["subject"],
                predicate=c["predicate"],
                fact_a_id=c["fact_a_id"],
                fact_b_id=c["fact_b_id"],
                source_a=c["source_a"],
                source_b=c["source_b"],
                ai_suggested_fact_id=winner["id"],
                ai_reason="Timestamp-based resolution (offline mode)",
            )
            reconciler.mark_auto_resolved(
                conflict["conflict_id"],
                winner["id"],
                "Offline auto-resolve (timestamp)",
            )
            println("Reconciler",
                    f"✅ Auto-resolved: conflict `{conflict['conflict_id']}` → fact #{winner['id']}",
                    "\033[1;32m")

    time.sleep(0.5)

    # Step 5: Human can query
    all_facts = keeper.list_all(limit=5)
    if all_facts:
        println("Keeper", f"📋 {len(all_facts)} fact(s) in store. Use `@keeper recall <subject>` to search.",
                "\033[1;36m")

    return {
        "status": "conflict_resolved",
        "conflicts": len(conflicts),
        "scenario": scenario,
        "resolution": result_text,
    }


def run_fixture(path: str) -> None:
    """Run one fixture file."""
    data = json.loads(Path(path).read_text())
    keeper = KeeperStore()
    reconciler = ReconcilerStore()
    reconciler.migrate_schema()

    # Clear DB
    keeper.clear()

    simulate_run(
        keeper=keeper,
        reconciler=reconciler,
        facts=data["facts"],
        scenario=data["scenario"],
        narration=data.get("narration", ""),
    )


def run_all() -> None:
    """Run all fixtures."""
    fixtures_dir = ROOT / "fixtures"
    fixture_files = sorted(fixtures_dir.glob("*.json"))

    if not fixture_files:
        println("DEMO", "❌ No fixtures found in fixtures/", "\033[1;31m")
        return

    for f in fixture_files:
        run_fixture(str(f))
        time.sleep(1)

    println("DEMO", "✅ All scenarios completed.", "\033[1;32m")


def main() -> None:
    if _HAS_BRIDGE:
        clear_events()

    if len(sys.argv) > 1:
        fixture = sys.argv[1]
        if Path(fixture).exists():
            run_fixture(fixture)
        else:
            print(f"❌ Fixture not found: {fixture}")
            sys.exit(1)
    else:
        run_all()


if __name__ == "__main__":
    main()
