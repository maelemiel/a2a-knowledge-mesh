"""Scraper Agent — Band-native scraper for git, Slack, Teams.

Commands:
  slurp git <path>     → scan repo files, extract facts, send to Keeper
  slurp slack <channel> → stub
  slurp teams <topic>   → stub

Extracted facts are sent to @Keeper in the same room.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent

logger = logging.getLogger(__name__)

KEEPER_ROOM_ID = os.getenv("BAND_KEEPER_ROOM_ID", "")


class ScraperAgent(BandAgent):
    agent_name = "Scraper"
    agent_description = "Scrapes git repos, Slack, Teams. Commands: slurp git/slack/teams"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._parsers = {
            "pyproject.toml": self._parse_pyproject,
            "package.json": self._parse_package_json,
            "Cargo.toml": self._parse_cargo_toml,
            "README.md": self._parse_readme,
            ".env.example": self._parse_env_example,
        }

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        if content.startswith("slurp git "):
            await self._cmd_slurp_git(content[10:].strip(), tools)
            return

        if content.startswith("slurp slack "):
            channel = content[12:].strip()
            await tools.send_message(
                f"⚠️ Slack scraping not implemented yet. Channel: {channel}"
            )
            return

        if content.startswith("slurp teams "):
            topic = content[12:].strip()
            await tools.send_message(
                f"⚠️ Teams scraping not implemented yet. Topic: {topic}"
            )
            return

        await tools.send_message(
            "🤖 Scraper commands:\n"
            "  `slurp git <path>`     → scan repo, send facts to Keeper\n"
            "  `slurp slack <chan>`   → (stub)\n"
            "  `slurp teams <topic>`  → (stub)"
        )

    async def _cmd_slurp_git(self, repo_path: str, tools: AgentToolsProtocol) -> None:
        path = Path(repo_path).expanduser().resolve()
        if not path.is_dir():
            await tools.send_message(f"❌ Not a directory: {path}")
            return

        await tools.send_message(f"🔍 Scanning {path.name}...")

        project_name = path.name
        facts: list[dict] = []

        for filepath in path.rglob("*"):
            if filepath.name in self._parsers:
                rel = filepath.relative_to(path)
                logger.info("Parsing %s", rel)
                parser = self._parsers[filepath.name]
                try:
                    parser(filepath, facts, str(rel), project_name)
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", rel, e)

        if not facts:
            await tools.send_message("📭 No facts extracted.")
            return

        # Send all facts in one batch message
        try:
            batch_json = json.dumps(facts)
            await tools.send_message(
                f"store-batch {batch_json}",
                mentions=[os.getenv("BAND_KEEPER_HANDLE", "Keeper")],
            )
            await tools.send_message(f"✅ Sent {len(facts)} facts to Keeper in 1 message")
        except Exception as e:
            await tools.send_message(f"❌ Failed to send batch: {e}")

    # ── Parsers ─────────────────────────────────────────────────────────

    def _parse_pyproject(self, path: Path, facts: list[dict], source_id: str, project_name: str) -> None:
        import tomllib
        data = tomllib.loads(path.read_text())
        proj = data.get("project", {})
        if proj.get("version"):
            facts.append({"subject": project_name, "predicate": "version",
                          "object": proj["version"], "source_id": source_id})
        for dep in proj.get("dependencies", []):
            facts.append({"subject": project_name, "predicate": "dep-python",
                          "object": dep, "source_id": source_id})

    def _parse_package_json(self, path: Path, facts: list[dict], source_id: str, project_name: str) -> None:
        data = json.loads(path.read_text())
        if data.get("version"):
            facts.append({"subject": project_name, "predicate": "version",
                          "object": data["version"], "source_id": source_id})
        for deps_key in ("dependencies", "devDependencies"):
            for dep_name, dep_ver in data.get(deps_key, {}).items():
                facts.append({"subject": project_name, "predicate": "dep-npm",
                              "object": f"{dep_name}@{dep_ver}",
                              "source_id": f"{source_id}/{deps_key}"})

    def _parse_cargo_toml(self, path: Path, facts: list[dict], source_id: str, project_name: str) -> None:
        import tomllib
        data = tomllib.loads(path.read_text())
        pkg = data.get("package", {})
        if pkg.get("version"):
            facts.append({"subject": project_name, "predicate": "version",
                          "object": pkg["version"], "source_id": source_id})
        for dep_name in data.get("dependencies", {}):
            facts.append({"subject": project_name, "predicate": "dep-cargo",
                          "object": dep_name, "source_id": source_id})

    def _parse_readme(self, path: Path, facts: list[dict], source_id: str, project_name: str) -> None:
        text = path.read_text(errors="replace")
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = m.group(1).strip() if m else path.stem
        facts.append({"subject": project_name, "predicate": "doc-title",
                      "object": title, "source_id": source_id})
        for match in re.finditer(
            r"(?:uses|built with|powered by|stack:\s*)([A-Z][A-Za-z0-9.\-+#]+)",
            text, re.IGNORECASE | re.MULTILINE,
        ):
            facts.append({"subject": project_name, "predicate": "uses",
                          "object": match.group(1).lower(), "source_id": source_id})

    def _parse_env_example(self, path: Path, facts: list[dict], source_id: str, project_name: str) -> None:
        text = path.read_text(errors="replace")
        for m in re.finditer(r"^(?:export\s+)?([A-Z][A-Z0-9_]+)\s*=", text, re.MULTILINE):
            facts.append({"subject": project_name, "predicate": "env-var",
                          "object": m.group(1), "source_id": source_id})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import os
    ScraperAgent(
        agent_id=os.getenv("BAND_SCRAPER_ID", ""),
        api_key=os.getenv("BAND_SCRAPER_KEY", ""),
    ).run()
