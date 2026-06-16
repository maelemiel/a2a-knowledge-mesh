#!/usr/bin/env python3
"""
Git Scraper — scanne un repo Git local, extrait des faits structurés
depuis les fichiers de code et documentation, et les envoie au Keeper via Band.

Usage:
  uv run python scripts/git_scraper.py /path/to/repo
  uv run python scripts/git_scraper.py              # répertoire courant

Flow:
  1. Scanne les fichiers pertinents (pyproject.toml, package.json, README.md, …)
  2. Extrait des faits structurés (subject=X predicate=Y object=Z)
  3. Envoie chaque fait dans la room Band où le KeeperAgent écoute
  4. Le Keeper reçoit "store subject=X predicate=Y object=Z source=ID" et stocke dans SQLite

Configuration (.env):
  BAND_AGENT_ID=   # ID de l'agent Band
  BAND_API_KEY=    # API key de l'agent Band
  BAND_KEEPER_ROOM_ID=  # Room ID où le KeeperAgent écoute
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ── Band API ──────────────────────────────────────────────────────────────

BAND_BASE_URL = os.getenv("BAND_BASE_URL", "https://app.band.ai")
BAND_AGENT_ID = os.getenv("BAND_AGENT_ID", "")
BAND_API_KEY = os.getenv("BAND_API_KEY", "")
BAND_KEEPER_ROOM_ID = os.getenv("BAND_KEEPER_ROOM_ID", "")

FACT_SEND_URL = (
    f"{BAND_BASE_URL}/api/v2/agents/{BAND_AGENT_ID}"
    f"/rooms/{BAND_KEEPER_ROOM_ID}/messages"
) if BAND_AGENT_ID and BAND_KEEPER_ROOM_ID else ""


# ── Parsers ───────────────────────────────────────────────────────────────


def parse_pyproject_toml(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les faits d'un pyproject.toml."""
    import tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    repo_name = project.get("name", source_id)

    if project.get("version"):
        facts.append({
            "subject": repo_name, "predicate": "version",
            "object": project["version"], "source_id": source_id,
        })
    if project.get("requires-python"):
        facts.append({
            "subject": repo_name, "predicate": "python-version",
            "object": project["requires-python"], "source_id": source_id,
        })

    for dep in project.get("dependencies", []):
        # Normalize: "package>=1.0" -> package name only
        pkg = re.split(r"[><=~!@]", dep)[0].strip().lower()
        if pkg:
            facts.append({
                "subject": repo_name, "predicate": "dependency",
                "object": pkg, "source_id": source_id,
            })

    for opt_name, opt_deps in project.get("optional-dependencies", {}).items():
        for dep in opt_deps:
            pkg = re.split(r"[><=~!@]", dep)[0].strip().lower()
            if pkg:
                facts.append({
                    "subject": repo_name, "predicate": f"optional-dependency:{opt_name}",
                    "object": pkg, "source_id": source_id,
                })


def parse_package_json(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les faits d'un package.json."""
    import json

    with open(path) as f:
        data = json.load(f)

    name = data.get("name", source_id)

    if data.get("version"):
        facts.append({
            "subject": name, "predicate": "version",
            "object": data["version"], "source_id": source_id,
        })

    for dep_key in ("dependencies", "devDependencies"):
        for dep_name, dep_ver in data.get(dep_key, {}).items():
            pred = f"npm-{dep_key.rstrip('s')}" if dep_key == "dependencies" else "npm-dev-dependency"
            facts.append({
                "subject": name, "predicate": pred,
                "object": f"{dep_name}@{dep_ver}", "source_id": f"{source_id}/{dep_key}",
            })


def parse_cargo_toml(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les faits d'un Cargo.toml."""
    import tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)

    pkg = data.get("package", {})
    name = pkg.get("name", source_id)

    if pkg.get("version"):
        facts.append({
            "subject": name, "predicate": "version",
            "object": pkg["version"], "source_id": source_id,
        })

    for dep_name in data.get("dependencies", {}):
        facts.append({
            "subject": name, "predicate": "cargo-dependency",
            "object": dep_name, "source_id": source_id,
        })


def parse_readme(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire des infos clés d'un fichier README/contribution doc."""
    text = path.read_text(errors="replace")

    # Titre (premier # heading)
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = m.group(1).strip() if m else path.stem
    facts.append({
        "subject": source_id, "predicate": "documentation-title",
        "object": title, "source_id": path.name,
    })

    # "requires Python" / "requires"
    m = re.search(r"requires\s+(?:Python|python)\s+([\d.]+)", text, re.IGNORECASE)
    if m:
        facts.append({
            "subject": source_id, "predicate": "requires-python",
            "object": m.group(1), "source_id": path.name,
        })

    # "uses" / "uses X"
    for match in re.finditer(r"(?:^|\s)(?:uses|built with|powered by|stack:\s*)([A-Za-z][A-Za-z0-9.\-+#]+)", text, re.IGNORECASE | re.MULTILINE):
        facts.append({
            "subject": source_id, "predicate": "uses",
            "object": match.group(1).lower(), "source_id": path.name,
        })

    # "port" references
    for match in re.finditer(r"(?:port|PORT)\s*[=:]\s*(\d{4,5})", text):
        facts.append({
            "subject": source_id, "predicate": "port",
            "object": match.group(1), "source_id": path.name,
        })


def parse_env_example(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les variables d'environnement d'un .env.example."""
    text = path.read_text(errors="replace")
    for m in re.finditer(r"^(export\s+)?([A-Z][A-Z0-9_]+)\s*[=:]?", text, re.MULTILINE):
        var_name = m.group(2)
        facts.append({
            "subject": source_id, "predicate": "env-variable",
            "object": var_name, "source_id": path.name,
        })


def parse_makefile(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les commandes principales d'un Makefile."""
    text = path.read_text(errors="replace")
    for m in re.finditer(r"^([a-zA-Z][a-zA-Z0-9_\-]+)\s*:", text, re.MULTILINE):
        cmd = m.group(1)
        if cmd in ("PHONY", ".PHONY", "SHELL", ".SILENT", "MAKEFLAGS", ".ONESHELL", ".DEFAULT_GOAL"):
            continue
        if cmd.startswith("export "):
            continue
        facts.append({
            "subject": source_id, "predicate": "make-target",
            "object": cmd, "source_id": path.name,
        })


def parse_docker_compose(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les services, ports, images d'un docker-compose.yml."""
    import yaml  # type: ignore[import-untyped]

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    for svc_name, svc in data.get("services", {}).items():
        # Service declaration
        facts.append({
            "subject": source_id, "predicate": "docker-service",
            "object": svc_name, "source_id": path.name,
        })

        # Image
        if svc.get("image"):
            facts.append({
                "subject": svc_name, "predicate": "docker-image",
                "object": svc["image"], "source_id": path.name,
            })

        # Ports
        for port_def in svc.get("ports", []):
            port_str = str(port_def)
            m = re.search(r"(?::|^)(\d{2,5})(?:$|:)", port_str)
            if m:
                facts.append({
                    "subject": svc_name, "predicate": "docker-port",
                    "object": m.group(1), "source_id": path.name,
                })

        # Build context
        if svc.get("build"):
            build_val = svc["build"]
            if isinstance(build_val, dict):
                ctx = build_val.get("context", str(build_val))
            else:
                ctx = str(build_val)
            facts.append({
                "subject": svc_name, "predicate": "docker-build-context",
                "object": ctx, "source_id": path.name,
            })


def parse_markdown_doc(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les titres et infos clés d'un fichier .md dans docs/."""
    rel = str(path.relative_to(source_id)) if source_id and path.is_relative_to(source_id) else path.name
    text = path.read_text(errors="replace")

    # Titres
    for m in re.finditer(r"^#{1,3}\s+(.+)$", text, re.MULTILINE):
        heading = m.group(1).strip()
        facts.append({
            "subject": rel, "predicate": "heading",
            "object": heading, "source_id": path.name,
        })

    # Ports
    for m in re.finditer(r"(?:port|PORT)\s*[=:]\s*(\d{4,5})", text):
        facts.append({
            "subject": rel, "predicate": "port",
            "object": m.group(1), "source_id": path.name,
        })

    # URLs
    for m in re.finditer(r"(https?://[^\s)\"'\]]+)", text):
        url = m.group(1).rstrip(".,;")
        facts.append({
            "subject": rel, "predicate": "url",
            "object": url, "source_id": path.name,
        })


# ── Scanner ───────────────────────────────────────────────────────────────


FILE_HANDLERS: dict[str, tuple] = {
    "pyproject.toml": (parse_pyproject_toml, "pyproject"),
    "package.json": (parse_package_json, "package.json"),
    "Cargo.toml": (parse_cargo_toml, "Cargo.toml"),
    "README.md": (parse_readme, "README"),
    "CONTRIBUTING.md": (parse_readme, "CONTRIBUTING"),
    ".env.example": (parse_env_example, ".env.example"),
    "Makefile": (parse_makefile, "Makefile"),
    "docker-compose.yml": (parse_docker_compose, "docker-compose"),
    "docker-compose.yaml": (parse_docker_compose, "docker-compose"),
}


def scan_repo(repo_path: Path) -> tuple[list[dict], int]:
    """Scanne un repo Git et retourne (facts, file_count)."""
    facts: list[dict] = []
    scanned_files = 0

    # 1. Fichiers nommés dans la racine
    for filename, (parser, source_id) in FILE_HANDLERS.items():
        fp = repo_path / filename
        if fp.is_file():
            try:
                parser(fp, facts, source_id + "-" + (repo_path.name or "repo"))
                scanned_files += 1
                logger.info("  ✓ %s (%d fact(s))", filename, len(facts))
            except Exception as e:
                logger.warning("  ⚠ %s: %s", filename, e)

    # 2. Fichiers .md dans docs/
    docs_dir = repo_path / "docs"
    if docs_dir.is_dir():
        for md_file in sorted(docs_dir.glob("*.md")):
            try:
                parse_markdown_doc(md_file, facts, str(repo_path))
                scanned_files += 1
            except Exception as e:
                logger.warning("  ⚠ %s: %s", md_file.name, e)

    # 3. Deduplication basique — même (subject, predicate, object, source_id) = skip
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict] = []
    for f in facts:
        key = (f["subject"], f["predicate"], f["object"], f["source_id"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    return deduped, scanned_files


# ── Envoi Band ────────────────────────────────────────────────────────────


_band_config_warned = False


async def send_to_band(client: httpx.AsyncClient, content: str) -> str | None:
    """Envoie un message texte dans la room Band du Keeper."""
    global _band_config_warned
    if not FACT_SEND_URL:
        if not _band_config_warned:
            logger.error("BAND_AGENT_ID, BAND_API_KEY, and BAND_KEEPER_ROOM_ID must be set")
            _band_config_warned = True
        return None

    payload = {"message": {"content": content}}
    headers = {
        "Authorization": f"Bearer {BAND_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = await client.post(FACT_SEND_URL, json=payload, headers=headers, timeout=10)
        if resp.is_success:
            return "sent"
        else:
            logger.warning("  ⚠ Band API %s: %s", resp.status_code, resp.text[:150])
            return None
    except Exception as e:
        logger.warning("  ⚠ Band send error: %s", e)
        return None


async def push_facts(facts: list[dict]) -> int:
    """Envoie les faits à Band par lots de 10."""
    if not facts:
        return 0

    sent = 0
    batch_size = 10
    async with httpx.AsyncClient() as client:
        for i in range(0, len(facts), batch_size):
            batch = facts[i : i + batch_size]
            for fact in batch:
                cmd = (
                    f"store subject={fact['subject']} "
                    f"predicate={fact['predicate']} "
                    f"object={fact['object']} "
                    f"source={fact['source_id']}"
                )
                result = await send_to_band(client, cmd)
                if result:
                    sent += 1

            # Petit délai entre les lots pour éviter le rate-limiting
            if i + batch_size < len(facts):
                await asyncio.sleep(0.3)

    return sent


# ── Mode cron ──────────────────────────────────────────────────────────────


CRON_INTERVAL = int(os.getenv("GIT_SCRAPER_INTERVAL", "0"))


async def run_cron(repo_path: Path) -> None:
    """Tourne en continu, scannant à intervalle régulier."""
    interval = CRON_INTERVAL
    if interval <= 0:
        interval = 300  # défaut: 5 min

    logger.info("🔄 Git Scraper cron mode — scanning every %ds", interval)
    while True:
        facts, file_count = scan_repo(repo_path)
        logger.info("📦 %d facts from %d files", len(facts), file_count)

        if facts:
            sent = await push_facts(facts)
            logger.info("✅ %d facts sent to Keeper via Band", sent)

        await asyncio.sleep(interval)


# ── Main ──────────────────────────────────────────────────────────────────


async def main() -> None:
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()

    if not target.is_dir():
        print(f"✗ Not a directory: {target}", file=sys.stderr)
        sys.exit(1)

    is_git = (target / ".git").is_dir()
    print(f"📂 Scanning: {target}" + (" (git repo)" if is_git else ""))
    print()

    # Valider la config Band
    missing = []
    for var in ("BAND_AGENT_ID", "BAND_API_KEY", "BAND_KEEPER_ROOM_ID"):
        if not os.getenv(var):
            missing.append(var)
    if missing:
        print(f"⚠️ Env vars manquantes: {', '.join(missing)}")
        print("   Les faits seront affichés mais pas envoyés à Band.")
        print()

    facts, file_count = scan_repo(target)

    print()
    print(f"📊 {len(facts)} facts extracted from {file_count} files")
    print()

    # Afficher un résumé
    by_file: dict[str, int] = {}
    for f in facts:
        by_file[f["source_id"]] = by_file.get(f["source_id"], 0) + 1

    for src, count in sorted(by_file.items(), key=lambda x: -x[1]):
        print(f"  • {src}: {count} fact(s)")

    print()

    if not facts:
        print("⚠️ No facts extracted — nothing to send.")
        return

    # Envoyer à Band
    sent = await push_facts(facts)
    if sent > 0:
        print(f"✅ {sent} facts stored in Keeper via Band")
    else:
        print("⚠️ 0 facts sent (check Band configuration)")
        print()
        # Fallback: afficher les commandes store
        print("📋 Facts (not sent — would send to Band):")
        for f in facts[:10]:
            print(f"  store subject={f['subject']} predicate={f['predicate']} object={f['object']} source={f['source_id']}")
        if len(facts) > 10:
            print(f"  ... and {len(facts) - 10} more")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Si mode cron
    if "--cron" in sys.argv:
        sys.argv.remove("--cron")
        target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
        asyncio.run(run_cron(target))
    else:
        asyncio.run(main())
