#!/usr/bin/env python3
"""Reset all SQLite databases — clean state for a new demo run."""

from pathlib import Path

DB_FILES = [
    Path(__file__).parent.parent / "data" / "keeper.db",
    Path(__file__).parent.parent / "data" / "reconciler.db",
    Path(__file__).parent.parent / "data" / "registry.db",
]

for db in DB_FILES:
    if db.exists():
        db.unlink()
        print(f"🧹 Deleted {db.name}")
    else:
        print(f"  — {db.name} not found")

print("✅ All databases reset.")
