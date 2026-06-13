#!/usr/bin/env python3
"""Run all 3 A2A agents concurrently."""

import sys, os, threading, signal
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.a2a_server import run_server
from agents.registry import SKILLS as REG_SKILLS, handle as handle_registry
from agents.keeper import SKILLS as KEEP_SKILLS, handle as handle_keeper
from agents.reconciler import SKILLS as REC_SKILLS, handle as handle_reconciler

REG_PORT = int(os.environ.get("REGISTRY_PORT", 8765))
KEEP_PORT = int(os.environ.get("KEEPER_PORT", 8766))
REC_PORT = int(os.environ.get("RECONCILER_PORT", 8767))


def start_thread(name, target, args):
    t = threading.Thread(target=target, args=args, daemon=True, name=name)
    t.start()
    return t


if __name__ == "__main__":
    print("=== A2A Knowledge Mesh ===")
    print(f"Registry:   http://localhost:{REG_PORT}/a2a")
    print(f"Keeper:     http://localhost:{KEEP_PORT}/a2a")
    print(f"Reconciler: http://localhost:{REC_PORT}/a2a")
    print("Press Ctrl+C to stop\n")

    threads = [
        start_thread("Registry", run_server,
                     ("Registry", "A2A agent directory", "1.0.0", REG_PORT, REG_SKILLS, handle_registry)),
        start_thread("Keeper",   run_server,
                     ("Keeper", "A2A fact storage", "1.0.0", KEEP_PORT, KEEP_SKILLS, handle_keeper)),
        start_thread("Reconciler", run_server,
                     ("Reconciler", "A2A conflict resolver", "1.0.0", REC_PORT, REC_SKILLS, handle_reconciler)),
    ]

    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    for t in threads:
        t.join()
