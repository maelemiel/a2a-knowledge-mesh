"""Keeper Agent — Band-native fact store.

Listens in Band rooms for commands like:

  @keeper store subject=X predicate=Y object=Z source=docs
  @keeper recall project-ALLY
  @keeper list

Stores facts in SQLite. Replies in the room.
"""

from __future__ import annotations

import logging

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent
from agents.keeper import KeeperStore

logger = logging.getLogger(__name__)


class KeeperAgent(BandAgent):
    agent_name = "Keeper"
    agent_description = "Structured fact store. Commands: store, recall, list, detect"

    def __init__(self) -> None:
        super().__init__()
        self.store = KeeperStore()

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        # Parse command: "store subject=X predicate=Y object=Z source=docs"
        if content.startswith("store "):
            await self._cmd_store(content[6:], tools)
            return

        if content.startswith("recall "):
            await self._cmd_recall(content[7:], tools)
            return

        if content == "list" or content == "list all":
            await self._cmd_list(tools)
            return

        if content == "detect":
            await self._cmd_detect(tools)
            return

        if content.startswith("get "):
            await self._cmd_get(content[4:], tools)
            return

        # Help
        await tools.send_message(
            "🤖 Keeper commands:\n"
            "  `store subject=X predicate=Y object=Z source=ID`\n"
            "  `recall <subject>`\n"
            "  `list`\n"
            "  `detect`\n"
            "  `get <id>`"
        )

    async def _cmd_store(self, args: str, tools: AgentToolsProtocol) -> None:
        params = _parse_kv(args)
        subject = params.get("subject") or ""
        predicate = params.get("predicate") or ""
        obj = params.get("object") or ""
        source = params.get("source", "band")

        if not subject or not predicate or not obj:
            await tools.send_message("⚠️ Usage: `store subject=X predicate=Y object=Z source=ID`")
            return

        result = self.store.store(
            subject=subject,
            predicate=predicate,
            object=obj,
            source_id=source,
        )
        await tools.send_message(
            f"✅ stored fact #{result['id']}: {subject} → {predicate} = {obj} (from {source})"
        )

    async def _cmd_recall(self, args: str, tools: AgentToolsProtocol) -> None:
        subject = args.strip() or None
        facts = self.store.recall(subject, limit=50 if subject is None else None)
        if not facts:
            await tools.send_message(f"📭 No facts for `{args.strip()}`")
            return
        lines = [f"📋 {len(facts)} fact(s):"]
        for f in facts[:15]:
            lines.append(
                f"  #{f['id']} [{f['source_id']}] {f['subject']} → {f['predicate']} = {f['object']}"
            )
        if len(facts) > 15:
            lines.append(f"  ... and {len(facts) - 15} more")
        await tools.send_message("\n".join(lines))

    async def _cmd_list(self, tools: AgentToolsProtocol) -> None:
        facts = self.store.list_all(limit=25)
        if not facts:
            await tools.send_message("📭 No facts stored yet.")
            return
        lines = [f"📋 {len(facts)} fact(s):"]
        for f in facts:
            lines.append(
                f"  #{f['id']} [{f['source_id']}] {f['subject']} → {f['predicate']} = {f['object']}"
            )
        await tools.send_message("\n".join(lines))

    async def _cmd_detect(self, tools: AgentToolsProtocol) -> None:
        conflicts = self.store.detect_conflicts()
        if not conflicts:
            await tools.send_message("✅ No conflicts found — all facts are consistent.")
            return
        lines = [f"⚠️ {len(conflicts)} conflict(s) detected:"]
        for c in conflicts:
            lines.append(
                f"  {c['subject']} ({c['predicate']}): "
                f"#{c['fact_a_id']} ({c['source_a']}) vs "
                f"#{c['fact_b_id']} ({c['source_b']})"
            )
        await tools.send_message("\n".join(lines))

    async def _cmd_get(self, args: str, tools: AgentToolsProtocol) -> None:
        try:
            fid = int(args.strip())
        except ValueError:
            await tools.send_message("⚠️ Usage: `get <id>`")
            return
        fact = self.store.get_fact(fid)
        if fact is None:
            await tools.send_message(f"📭 Fact #{fid} not found")
            return
        await tools.send_message(
            f"#{fact['id']} {fact['subject']} → {fact['predicate']} = {fact['object']}\n"
            f"  source: {fact['source_id']} | version: {fact['version']}"
        )


def _parse_kv(text: str) -> dict[str, str]:
    """Parse 'subject=X predicate=Y object=Z' into dict."""
    result = {}
    for part in text.split():
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    KeeperAgent().run()
