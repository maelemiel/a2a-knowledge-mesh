"""Runner — launch all 3 agents or a single one with auth."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def run_single(agent_name: str) -> None:
    if agent_name == "registry":
        from agents.registry import RegistryAgent

        RegistryAgent().run()
    elif agent_name == "keeper":
        from agents.keeper import KeeperAgent

        KeeperAgent().run()
    elif agent_name == "reconciler":
        from agents.reconciler import ReconcilerAgent

        band_id = os.getenv("BAND_AGENT_ID")
        band_key = os.getenv("BAND_API_KEY")
        ReconcilerAgent(band_agent_id=band_id, band_api_key=band_key).run()
    else:
        print(f"unknown agent: {agent_name}", file=sys.stderr)
        sys.exit(1)


def run_all() -> None:
    """Launch 3 agents as subprocesses in the same terminal group.

    Each agent picks up its bearer token from the environment:
    - Registry  → ``A2A_REGISTRY_TOKEN``
    - Keeper    → ``A2A_KEEPER_TOKEN``
    - Reconciler → ``A2A_RECONCILER_TOKEN``
    """
    root = Path(__file__).parent.parent
    procs: list[subprocess.Popen] = []

    def cleanup(_sig=None, _frame=None) -> None:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Validate env
    missing_tokens = []
    for role in ("registry", "keeper", "reconciler"):
        env_var = f"A2A_{role.upper()}_TOKEN"
        if not os.getenv(env_var):
            missing_tokens.append(env_var)
    if missing_tokens:
        print(
            f"⚠ Missing auth tokens: {', '.join(missing_tokens)}\n"
            "  Agents will start but non-public endpoints require tokens.\n"
            "  Set them in .env or export before running.",
            file=sys.stderr,
        )

    for name in ["registry", "keeper", "reconciler"]:
        p = subprocess.Popen(
            [sys.executable, "-m", f"agents.{name}"],
            cwd=root,
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
        cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="A2A Knowledge Mesh runner")
    parser.add_argument(
        "--agent",
        "-a",
        choices=["registry", "keeper", "reconciler"],
        help="Run a single agent (default: all 3)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.agent:
        run_single(args.agent)
    else:
        run_all()


if __name__ == "__main__":
    main()
