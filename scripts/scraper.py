"""
Scraper autonome — lit un pyproject.toml et push les faits dans le Keeper.

Usage:
  export A2A_KEEPER_TOKEN="<token>"   # ou A2A_MASTER_TOKEN
  uv run python scripts/scraper.py /path/to/project

Sans argument, scrappe le projet courant.
"""

import os
import sys
import json
import httpx
import asyncio

KEEPER_URL = "http://localhost:8766/a2a"
KEEPER_TOKEN = os.getenv("A2A_KEEPER_TOKEN") or os.getenv("A2A_MASTER_TOKEN", "")


async def push_fact(subject: str, predicate: str, obj: str, source: str):
    """POST un fait au Keeper via JSON-RPC 2.0 authentifié."""
    payload = {
        "jsonrpc": "2.0",
        "id": "scraper-push",
        "method": "store-fact",
        "params": {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "source_id": source,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KEEPER_TOKEN}",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(KEEPER_URL, json=payload, headers=headers)
        if r.status_code == 200:
            data = r.json()
            if "result" in data:
                print(f"  ✓ {subject} → {predicate} = {obj}")
            else:
                print(f"  ✗ {data.get('error', {}).get('message', 'unknown error')}")
        else:
            print(f"  ✗ HTTP {r.status_code}: {r.text[:200]}")


async def push_batch(facts: list[dict]):
    """POST un lot de faits au Keeper."""
    payload = {
        "jsonrpc": "2.0",
        "id": "scraper-batch",
        "method": "store-facts-batch",
        "params": {"facts": facts},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KEEPER_TOKEN}",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(KEEPER_URL, json=payload, headers=headers)
        if r.status_code == 200:
            data = r.json()
            count = len(data.get("result", {}).get("facts", []))
            print(f"  ✓ lot de {count} faits stocké")
        else:
            print(f"  ✗ lot refusé (HTTP {r.status_code})")


def parse_pyproject(path: str) -> list[dict]:
    """Extrait les faits d'un pyproject.toml."""
    import tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    name = project.get("name", os.path.basename(os.path.dirname(path)))
    facts = []

    # Métadonnées du projet
    if project.get("requires-python"):
        facts.append({
            "subject": name, "predicate": "python-version",
            "object": project["requires-python"], "source_id": "scraper-pyproject",
            "source_url": f"file://{path}",
        })

    if project.get("version"):
        facts.append({
            "subject": name, "predicate": "version",
            "object": project["version"], "source_id": "scraper-pyproject",
            "source_url": f"file://{path}",
        })

    # Dépendances principales
    for dep in project.get("dependencies", []):
        facts.append({
            "subject": name, "predicate": "dependency",
            "object": dep, "source_id": "scraper-pyproject",
            "source_url": f"file://{path}",
        })

    # Dépendances optionnelles
    for opt_name, opt_deps in project.get("optional-dependencies", {}).items():
        for dep in opt_deps:
            facts.append({
                "subject": name, "predicate": f"optional-dependency:{opt_name}",
                "object": dep, "source_id": "scraper-pyproject",
                "source_url": f"file://{path}",
            })

    return facts


async def scrape_project(project_path: str):
    """Scanne un projet et pousse les faits."""
    toml_path = os.path.join(project_path, "pyproject.toml")
    if not os.path.exists(toml_path):
        print(f"✗ pyproject.toml introuvable dans {project_path}")
        return

    print(f"🔍 Scraping {toml_path}...")
    facts = parse_pyproject(toml_path)
    print(f"   {len(facts)} fait(s) extrait(s)")

    if not facts:
        print("   rien à pusher")
        return

    # Batch push (un seul appel HTTP)
    await push_batch(facts)
    print(f"✅ Terminé — {len(facts)} faits dans le Keeper")


async def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    print(f"📦 Scraper A2A — cible: {target}")
    await scrape_project(target)


if __name__ == "__main__":
    asyncio.run(main())
