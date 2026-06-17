"""Scraper Agent — LLM-powered repo analyst on Band.

Listens for commands and auto-scans the configured repo on bootstrap.
Uses LLM (via Featherless/AI/ML API) to extract structured facts from
any file — code, docs, configs — then sends them to Keeper via Band.

Commands:
  @scraper scan [path]    → LLM-scan a repo, send facts to Keeper
  @scraper status         → show last scan results
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent
from agents.provider import Provider, resolve_config
from agents.scraper_service import scan_repo

logger = logging.getLogger(__name__)

REPO_PATH = os.getenv("SCRAPER_REPO_PATH", "")

# Number of facts per batch message (Band has message size limits)
_BATCH_SIZE = 50


class ScraperAgent(BandAgent):
    agent_name = "Scraper"
    agent_description = (
        "LLM-powered repo analyst. "
        "Commands: scan [path], status"
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._provider = Provider(timeout=60.0, max_retries=2)
        self._config = resolve_config()
        self._last_scan: dict | None = None

    async def on_bootstrap(self, room_id: str, tools: AgentToolsProtocol) -> None:
        """Auto-scan the configured repo on startup."""
        await tools.send_message(
            f"🤖 **{self.agent_name}** en ligne — {self.agent_description}"
        )

        target = REPO_PATH
        if target:
            if self._config is None:
                await tools.send_message(
                    "⚠️ No LLM provider configured (FEATHERLESS_API_KEY). "
                    "Use `scan <path>` once configured."
                )
                return
            await tools.send_message(
                f"🔍 Auto-scanning `{Path(target).name}` with LLM..."
            )
            await self._do_scan(target, tools)

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        if content.startswith("scan "):
            path = content[5:].strip()
            await self._do_scan(path or REPO_PATH, tools)
            return

        if content == "scan" and REPO_PATH:
            await self._do_scan(REPO_PATH, tools)
            return

        if content == "status":
            await self._cmd_status(tools)
            return

        if content == "scan ." or content == "scan self":
            await tools.send_message("🔍 Scanning own repo...")
            await self._do_scan(str(Path(__file__).parent.parent), tools)
            return

        await tools.send_message(
            "🤖 Scraper commands:\n"
            "  `scan <path>`     → LLM-scan a repo, facts to Keeper\n"
            "  `scan self`       → scan our own repo\n"
            "  `status`          → last scan results"
        )

    # ── Scan logic ─────────────────────────────────────────────────────

    async def _do_scan(self, repo_path: str, tools: AgentToolsProtocol) -> None:
        if not repo_path or repo_path == "''":
            await tools.send_message("❌ No repo path. Use `scan <path>` or set SCRAPER_REPO_PATH.")
            return

        path = Path(repo_path).expanduser()
        if not path.is_dir():
            await tools.send_message(f"❌ Not a directory: {path}")
            return

        if self._config is None:
            await tools.send_message(
                "❌ No LLM provider. Set FEATHERLESS_API_KEY in .env"
            )
            return

        await tools.send_message(
            f"🔍 Scanning `{path.name}` with LLM (Featherless)..."
        )

        try:
            result = await scan_repo(path, self._provider, self._config)
        except Exception as e:
            logger.exception("Scan failed")
            await tools.send_message(f"❌ Scan error: {e}")
            return

        self._last_scan = result
        facts = result.get("facts", [])
        conflicts = result.get("conflicts", [])

        await tools.send_message(
            f"📊 Scan complete: {result['total_facts']} facts "
            f"from {result['files_analyzed']} files "
            f"({result['code_files']} code, {result['doc_files']} doc)"
        )

        # ── Send facts to Keeper in batches ───────────────────────────
        if not facts:
            await tools.send_message("📭 No facts extracted.")
            return

        keeper = os.getenv("BAND_KEEPER_HANDLE", "keeper")

        for i in range(0, len(facts), _BATCH_SIZE):
            batch = facts[i:i + _BATCH_SIZE]
            try:
                batch_json = json.dumps(batch, ensure_ascii=False)
                await tools.send_message(
                    f"store-batch {batch_json}",
                    mentions=[keeper],
                )
            except Exception as e:
                logger.warning("Batch send error: %s", e)
                # Fallback: send one by one
                for fact in batch:
                    try:
                        kv = (
                            f"subject={fact.get('subject', '')} "
                            f"predicate={fact.get('predicate', '')} "
                            f"object={fact.get('object', '')} "
                            f"source={fact.get('source_id', 'scraper')}"
                        )
                        await tools.send_message(
                            f"store {kv}",
                            mentions=[keeper],
                        )
                    except Exception as e2:
                        logger.warning("Fact send error: %s", e2)

        await tools.send_message(
            f"✅ Sent {len(facts)} facts to @{keeper} "
            f"({(len(facts) + _BATCH_SIZE - 1) // _BATCH_SIZE} messages)"
        )

        # ── Report code-vs-doc conflicts ──────────────────────────────
        if conflicts:
            lines = [f"⚠️ {len(conflicts)} code-vs-doc conflict(s) detected:"]
            for c in conflicts[:5]:
                lines.append(
                    f"  • `{c.get('subject', '?')}` → {c.get('predicate', '?')}: "
                    f"'{c.get('code_value', '?')}' vs "
                    f"'{c.get('doc_value', '?')}' "
                    f"[{c.get('severity', 'MEDIUM')}]"
                )
            if len(conflicts) > 5:
                lines.append(f"  ... and {len(conflicts) - 5} more")

            reconciler = os.getenv("BAND_RECONCILER_HANDLE", "reconciler")
            await tools.send_message(
                "\n".join(lines),
                mentions=[reconciler],
            )
        else:
            await tools.send_message(
                "✅ No code-vs-doc conflicts — code and docs agree."
            )

    async def _cmd_status(self, tools: AgentToolsProtocol) -> None:
        if not self._last_scan:
            await tools.send_message("📭 No scan performed yet.")
            return

        r = self._last_scan
        await tools.send_message(
            f"📊 Last scan: `{r.get('repo_name', '?')}`\n"
            f"  • {r['total_facts']} facts from {r['files_analyzed']} files\n"
            f"  • {r['code_files']} code files, {r['doc_files']} doc files\n"
            f"  • {len(r.get('conflicts', []))} code-vs-doc conflict(s)"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import os
    ScraperAgent(
        agent_id=os.getenv("BAND_SCRAPER_ID", ""),
        api_key=os.getenv("BAND_SCRAPER_KEY", ""),
    ).run()
