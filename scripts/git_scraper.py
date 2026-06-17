#!/usr/bin/env python3
"""Git Scraper — scanne un repo Git local, extrait des faits structurés
depuis les fichiers de code ET de documentation, et les pousse au Keeper
via A2A JSON-RPC (direct) ou Band (fallback).

Chaque fait est taggé avec source_type (code|doc) et source_url (file://).
Le Reconciler utilise ces métadonnées pour détecter code vs doc contradictions.

Usage:
  uv run python scripts/git_scraper.py /path/to/repo
  uv run python scripts/git_scraper.py              # répertoire courant
  uv run python scripts/git_scraper.py --dry-run    # affiche sans envoyer

Push methods (auto-detected by priority):
  1. A2A direct → Keeper /a2a (JSON-RPC 2.0, store-facts-batch)
  2. Band room  → Band chat room message
  3. Dry-run    → affiche les faits dans stdout
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import hashlib
import hmac
import json
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ── Push config ─────────────────────────────────────────────────────────

KEEPER_URL = os.getenv("KEEPER_URL", "http://localhost:8766/a2a")
KEEPER_TOKEN = os.getenv("A2A_KEEPER_TOKEN") or os.getenv("A2A_MASTER_TOKEN", "")

BAND_BASE_URL = os.getenv("BAND_BASE_URL", "https://app.band.ai")
BAND_AGENT_ID = os.getenv("BAND_AGENT_ID", "")
BAND_API_KEY = os.getenv("BAND_API_KEY", "")
BAND_KEEPER_ROOM_ID = os.getenv("BAND_KEEPER_ROOM_ID", "")

# HMAC signing (required by Keeper auth middleware)
_HMAC_SECRET: bytes = os.getenv("A2A_HMAC_SECRET", "").encode("ascii")


def _sign_body(body: bytes) -> str:
    """HMAC-SHA256 sign request body."""
    if not _HMAC_SECRET:
        return ""
    return hmac.new(_HMAC_SECRET, body, hashlib.sha256).hexdigest()

FACT_SEND_URL = (
    f"{BAND_BASE_URL}/api/v2/agents/{BAND_AGENT_ID}"
    f"/rooms/{BAND_KEEPER_ROOM_ID}/messages"
) if BAND_AGENT_ID and BAND_KEEPER_ROOM_ID else ""


# ── Source type helpers ──────────────────────────────────────────────────

CODE_FILES = {
    "pyproject.toml", "package.json", "Cargo.toml", "Makefile",
    "docker-compose.yml", "docker-compose.yaml", ".env.example",
    "composer.json", "Gemfile", "Pipfile", "requirements.txt",
    "go.mod", "go.sum", "build.gradle", "BUILD",
}

DOC_FILES = {
    "README.md", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE",
    "ARCHITECTURE.md", "DESIGN.md", "AGENTS.md",
}


def source_type(filename: str) -> str:
    """Determine si un fichier est code ou doc."""
    if filename in CODE_FILES:
        return "code"
    if filename in DOC_FILES or filename.endswith(".md"):
        return "doc"
    return "code"  # défaut


def make_fact(subject: str, predicate: str, obj: str,
              source_id: str, file_path: str) -> dict:
    """Construit un fait structuré avec métadonnées."""
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "source_id": source_id,
        "source_url": f"file://{file_path}",
        "source_type": source_type(Path(file_path).name),
    }


# ── Parsers ──────────────────────────────────────────────────────────────


def parse_pyproject_toml(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les faits d'un pyproject.toml."""
    import tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    repo_name = project.get("name", source_id)

    if project.get("version"):
        facts.append(make_fact(repo_name, "version", project["version"],
                                source_id, str(path)))
    if project.get("requires-python"):
        facts.append(make_fact(repo_name, "python-version", project["requires-python"],
                                source_id, str(path)))

    for dep in project.get("dependencies", []):
        pkg = re.split(r"[><=~!@]", dep)[0].strip().lower()
        if pkg:
            facts.append(make_fact(repo_name, "dependency", pkg,
                                    source_id, str(path)))

    for opt_name, opt_deps in project.get("optional-dependencies", {}).items():
        for dep in opt_deps:
            pkg = re.split(r"[><=~!@]", dep)[0].strip().lower()
            if pkg:
                facts.append(make_fact(repo_name, f"optional-dependency:{opt_name}", pkg,
                                        source_id, str(path)))


def parse_package_json(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les faits d'un package.json."""
    import json

    with open(path) as f:
        data = json.load(f)

    name = data.get("name", source_id)

    if data.get("version"):
        facts.append(make_fact(name, "version", data["version"],
                                source_id, str(path)))

    for dep_key in ("dependencies", "devDependencies"):
        for dep_name, dep_ver in data.get(dep_key, {}).items():
            pred = "npm-dependency" if dep_key == "dependencies" else "npm-dev-dependency"
            facts.append(make_fact(name, pred, f"{dep_name}@{dep_ver}",
                                    source_id, str(path)))


def parse_cargo_toml(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les faits d'un Cargo.toml."""
    import tomllib

    with open(path, "rb") as f:
        data = tomllib.load(f)

    pkg = data.get("package", {})
    name = pkg.get("name", source_id)

    if pkg.get("version"):
        facts.append(make_fact(name, "version", pkg["version"],
                                source_id, str(path)))

    for dep_name in data.get("dependencies", {}):
        facts.append(make_fact(name, "cargo-dependency", dep_name,
                                source_id, str(path)))


def parse_readme(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire des infos clés d'un fichier README/contribution doc."""
    text = path.read_text(errors="replace")

    # Titre (premier # heading)
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = m.group(1).strip() if m else path.stem
    facts.append(make_fact(source_id, "documentation-title", title,
                            path.name, str(path)))

    # "requires Python" / "requires"
    for m in re.finditer(r"requires\s+(?:Python|python)\s+([\d.]+)", text, re.IGNORECASE):
        facts.append(make_fact(source_id, "requires-python", m.group(1),
                                path.name, str(path)))

    # "uses" / "uses X"
    for m in re.finditer(
        r"(?:^|\s)(?:uses|built with|powered by|stack:\s*)"
        r"([A-Za-z][A-Za-z0-9.\-+#]+)",
        text, re.IGNORECASE | re.MULTILINE,
    ):
        facts.append(make_fact(source_id, "uses", m.group(1).lower(),
                                path.name, str(path)))

    # "port" references
    for m in re.finditer(r"(?:port|PORT)\s*[=:]\s*(\d{4,5})", text):
        facts.append(make_fact(source_id, "port", m.group(1),
                                path.name, str(path)))


def parse_env_example(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les variables d'environnement d'un .env.example."""
    text = path.read_text(errors="replace")
    for m in re.finditer(r"^(export\s+)?([A-Z][A-Z0-9_]+)\s*[=:]?", text, re.MULTILINE):
        var_name = m.group(2)
        facts.append(make_fact(source_id, "env-variable", var_name,
                                path.name, str(path)))


def parse_makefile(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les commandes principales d'un Makefile."""
    text = path.read_text(errors="replace")
    skip_targets = {"PHONY", ".PHONY", "SHELL", ".SILENT",
                    "MAKEFLAGS", ".ONESHELL", ".DEFAULT_GOAL"}
    for m in re.finditer(r"^([a-zA-Z][a-zA-Z0-9_\-]+)\s*:", text, re.MULTILINE):
        cmd = m.group(1)
        if cmd in skip_targets or cmd.startswith("export "):
            continue
        facts.append(make_fact(source_id, "make-target", cmd,
                                path.name, str(path)))


def parse_docker_compose(path: Path, facts: list[dict], source_id: str) -> None:
    """Extraire les services, ports, images d'un docker-compose.yml."""
    import yaml  # type: ignore[import-untyped]

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    for svc_name, svc in data.get("services", {}).items():
        facts.append(make_fact(source_id, "docker-service", svc_name,
                                path.name, str(path)))
        if svc.get("image"):
            facts.append(make_fact(svc_name, "docker-image", svc["image"],
                                    path.name, str(path)))
        for port_def in svc.get("ports", []):
            port_str = str(port_def)
            m = re.search(r"(?::|^)(\d{2,5})(?:$|:)", port_str)
            if m:
                facts.append(make_fact(svc_name, "docker-port", m.group(1),
                                        path.name, str(path)))
        if svc.get("build"):
            build_val = svc["build"]
            ctx = str(build_val.get("context", build_val)) if isinstance(build_val, dict) else str(build_val)
            facts.append(make_fact(svc_name, "docker-build-context", ctx,
                                    path.name, str(path)))


def parse_markdown_doc(path: Path, facts: list[dict], repo_root: str) -> None:
    """Extraire les titres et infos clés d'un fichier .md dans docs/."""
    rel = str(path.relative_to(repo_root)) if repo_root else path.name
    text = path.read_text(errors="replace")

    for m in re.finditer(r"^#{1,3}\s+(.+)$", text, re.MULTILINE):
        heading = m.group(1).strip()
        facts.append(make_fact(rel, "heading", heading,
                                path.name, str(path)))

    for m in re.finditer(r"(?:port|PORT)\s*[=:]\s*(\d{4,5})", text):
        facts.append(make_fact(rel, "port", m.group(1),
                                path.name, str(path)))

    for m in re.finditer(r"(https?://[^\s)\"'\]]+)", text):
        url = m.group(1).rstrip(".,;")
        facts.append(make_fact(rel, "url", url,
                                path.name, str(path)))


# ── Scanner ──────────────────────────────────────────────────────────────

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
    for filename, (parser, src_label) in FILE_HANDLERS.items():
        fp = repo_path / filename
        if fp.is_file():
            try:
                source_id = f"{src_label}-{repo_path.name}"
                parser(fp, facts, source_id)
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


# ── Push: A2A direct (JSON-RPC 2.0) ─────────────────────────────────────


async def push_a2a(facts: list[dict]) -> int:
    """Pousse les faits au Keeper via A2A JSON-RPC 2.0 store-facts-batch."""
    if not facts:
        return 0

    payload = {
        "jsonrpc": "2.0",
        "id": "git-scraper-batch",
        "method": "store-facts-batch",
        "params": {"facts": facts},
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KEEPER_TOKEN}",
    }
    sig = _sign_body(body_bytes)
    if sig:
        headers["X-A2A-Signature"] = sig

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(KEEPER_URL, content=body_bytes, headers=headers, timeout=10)
            if resp.is_success:
                data = resp.json()
                if "result" in data:
                    count = len(data["result"].get("facts", []))
                    logger.info("  ✅ %d facts pushed via A2A", count)
                    return count
                else:
                    err = data.get("error", {}).get("message", "unknown")
                    logger.warning("  ⚠ A2A error: %s", err)
                    return 0
            else:
                logger.warning("  ⚠ A2A HTTP %s: %s", resp.status_code, resp.text[:200])
                return 0
        except Exception as e:
            logger.warning("  ⚠ A2A connection error: %s", e)
            return 0


# ── Push: Band room ─────────────────────────────────────────────────────


async def send_to_band(client: httpx.AsyncClient, content: str) -> str | None:
    """Envoie un message texte dans la room Band du Keeper."""
    if not FACT_SEND_URL:
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
        logger.warning("  ⚠ Band API %s: %s", resp.status_code, resp.text[:150])
        return None
    except Exception as e:
        logger.warning("  ⚠ Band send error: %s", e)
        return None


async def push_band(facts: list[dict]) -> int:
    """Envoie les faits à Band par lots de 10."""
    if not facts:
        return 0

    sent = 0
    batch_size = 10
    async with httpx.AsyncClient() as client:
        for i in range(0, len(facts), batch_size):
            batch = facts[i: i + batch_size]
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
            if i + batch_size < len(facts):
                await asyncio.sleep(0.3)

    return sent


# ── Dispatcher ───────────────────────────────────────────────────────────


async def push_facts(facts: list[dict]) -> int:
    """Pousse les faits — A2A direct > Band > dry-run."""
    if not facts:
        return 0

    # 1. Try A2A direct
    if KEEPER_TOKEN:
        sent = await push_a2a(facts)
        if sent > 0:
            return sent

    # 2. Try Band room
    if FACT_SEND_URL and BAND_API_KEY:
        sent = await push_band(facts)
        if sent > 0:
            return sent

    logger.warning("  ⚠ No push method available — facts not sent")
    return 0


# ── Mode cron ────────────────────────────────────────────────────────────

CRON_INTERVAL = int(os.getenv("GIT_SCRAPER_INTERVAL", "0"))


async def run_cron(repo_path: Path) -> None:
    """Tourne en continu, scannant à intervalle régulier."""
    interval = CRON_INTERVAL if CRON_INTERVAL > 0 else 300

    logger.info("🔄 Git Scraper cron mode — scanning every %ds", interval)
    while True:
        facts, file_count = scan_repo(repo_path)
        logger.info("📦 %d facts from %d files", len(facts), file_count)
        if facts:
            sent = await push_facts(facts)
            logger.info("✅ %d facts sent to Keeper", sent)
        await asyncio.sleep(interval)


# ── Main ─────────────────────────────────────────────────────────────────


async def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        sys.argv.remove("--dry-run")

    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    if not target.is_dir():
        print(f"✗ Not a directory: {target}", file=sys.stderr)
        sys.exit(1)

    is_git = (target / ".git").is_dir()
    print(f"📂 Scanning: {target}" + (" (git repo)" if is_git else ""))
    print()

    # Afficher le mode de push
    if dry_run:
        print("🏷️  DRY-RUN — facts will be printed, not sent")
    elif KEEPER_TOKEN:
        print(f"🔌 Push: A2A direct → {KEEPER_URL}")
    elif FACT_SEND_URL and BAND_API_KEY:
        print(f"🔌 Push: Band room → {BAND_KEEPER_ROOM_ID}")
    else:
        print("⚠️  No push method configured — facts will be printed only")
        print("   Set A2A_KEEPER_TOKEN or BAND_* env vars in .env")
    print()

    facts, file_count = scan_repo(target)
    print()
    print(f"📊 {len(facts)} facts extracted from {file_count} files")
    print()

    # Résumé par fichier
    by_file: dict[str, tuple[int, str]] = {}
    for f in facts:
        src = f["source_id"]
        cnt, _ = by_file.get(src, (0, ""))
        by_file[src] = (cnt + 1, f["source_type"])

    for src, (count, stype) in sorted(by_file.items(), key=lambda x: -x[1][0]):
        icon = "📄" if stype == "code" else "📝"
        print(f"  {icon} {src}: {count} fact(s) [{stype}]")

    print()

    if not facts:
        print("⚠️ No facts extracted — nothing to send.")
        return

    if dry_run:
        print("📋 Facts (dry-run, top 15):")
        for f in facts[:15]:
            tag = f.get("source_type", "?")
            print(f"  [{tag}] {f['subject']} → {f['predicate']} = {f['object']}  ({f['source_id']})")
        if len(facts) > 15:
            print(f"  ... and {len(facts) - 15} more")
        print(f"\n✅ Dry-run complete — {len(facts)} facts would be sent")
        return

    sent = await push_facts(facts)
    if sent > 0:
        print(f"✅ {sent} facts stored in Keeper")
    else:
        print("⚠️ 0 facts sent (check configuration)")
        print()
        print("📋 Facts (not sent, top 10):")
        for f in facts[:10]:
            tag = f.get("source_type", "?")
            print(f"  [{tag}] {f['subject']} → {f['predicate']} = {f['object']}  ({f['source_id']})")
        if len(facts) > 10:
            print(f"  ... and {len(facts) - 10} more")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--cron" in sys.argv:
        sys.argv.remove("--cron")
        target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
        asyncio.run(run_cron(target))
    else:
        asyncio.run(main())
