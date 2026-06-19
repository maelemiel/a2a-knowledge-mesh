"""Reconciler Agent — Band-native conflict resolver with LLM suggestions.

Listens for conflict reports from Keeper, creates Band rooms,
@mentions agents, suggests a winner via LLM, and records resolutions.

Commands:
  @reconciler detect          → scan Keeper DB, LLM suggests winner
  @reconciler status          → show open/closed conflicts with AI suggestions
  @reconciler clear           → clear recorded conflicts for a fresh demo
  @reconciler resolve <id> <fact_id> [reason]  → record resolution
  @reconciler resolve-all [ai] → safely apply AI suggestions to all open conflicts
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent
from agents.reconciler import (
    ReconcilerStore,
    _llm_is_real_conflict,
    _llm_suggest,
    _llm_score_conflict,
    _llm_root_cause,
    _build_conflict_message,
)

logger = logging.getLogger(__name__)


def _is_detect_request(content: str) -> bool:
    """Recognize direct commands and structured Keeper handoffs."""
    normalized = content.strip()
    return (
        normalized.startswith("detect")
        or "handoff: conflict.detected" in normalized
        or '"type": "conflict.detected"' in normalized
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ReconcilerAgent(BandAgent):
    agent_name = "Reconciler"
    agent_description = (
        "Conflict resolver with AI suggestions. Commands: detect, status, resolve, resolve-all"
    )

    def __init__(self, keeper_db: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.store = ReconcilerStore()
        self.store.migrate_schema()
        # Keeper DB path: used for local standalone conflict scans.
        # In production, conflicts are detected by Keeper and pushed via Band @mentions.
        self.keeper_db_path = (
            keeper_db
            or os.getenv("KEEPER_DB_PATH", "")
            or str(Path(__file__).parent.parent / "data" / "keeper.db")
        )

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        if _is_detect_request(content):
            await self._cmd_detect(tools)
            return

        if content == "status":
            await self._cmd_status(tools)
            return

        if content == "clear":
            await self._cmd_clear(tools)
            return

        if content == "resolve-all" or content.startswith("resolve-all "):
            await self._cmd_resolve_all(content[len("resolve-all"):].strip(), tools)
            return

        if content.startswith("resolve "):
            await self._cmd_resolve(content[8:], tools)
            return

        await tools.send_message(
            "🤖 Reconciler commands:\n"
            "  `detect`            → scan Keeper DB, AI suggests winner\n"
            "  `status`            → show open/closed conflicts\n"
            "  `clear`             → clear recorded conflict history\n"
            "  `resolve-all [ai]`  → apply valid AI suggestions to every open conflict\n"
            "  `resolve <id> <fact> [reason]`  → record a resolution"
        )

    async def _cmd_detect(self, tools: AgentToolsProtocol) -> None:
        """Read Keeper's SQLite DB, find conflicts, ask LLM for suggestions.

        Pipeline:
          1. SQL JOIN → candidate pairs
          2. MAE-53: LLM semantic filter (is this a real conflict?)
          3. If real conflict: LLM suggest winner
          4. MAE-54: LLM score + auto-resolve
          5. MAE-55: LLM root cause analysis
          6. Build rich message
        """
        if not os.path.exists(self.keeper_db_path):
            await tools.send_message("⚠️ Keeper DB not found. Is Keeper running?")
            return

        conn = sqlite3.connect(self.keeper_db_path)
        try:
            rows = conn.execute("""
                SELECT f1.subject, f1.predicate,
                       f1.id, f1.object, f1.source_id, f1.timestamp,
                       f2.id, f2.object, f2.source_id, f2.timestamp
                FROM facts f1
                JOIN facts f2 ON f1.subject = f2.subject
                             AND f1.predicate = f2.predicate
                             AND f1.source_id < f2.source_id
                             AND f1.object != f2.object
                ORDER BY f1.subject, f1.predicate
            """).fetchall()
        finally:
            conn.close()

        if not rows:
            await tools.send_message("✅ No conflicts found.")
            return

        created = []
        messages = []
        skipped_existing = 0

        for r in rows:
            subject, predicate = r[0], r[1]
            fa = {
                "id": r[2],
                "object": r[3],
                "source_id": r[4],
                "timestamp": r[5],
                "subject": subject,
                "predicate": predicate,
            }
            fb = {
                "id": r[6],
                "object": r[7],
                "source_id": r[8],
                "timestamp": r[9],
                "subject": subject,
                "predicate": predicate,
            }

            existing = self.store.get_conflict_for_pair(fa["id"], fb["id"])
            if existing:
                skipped_existing += 1
                continue

            # ---------------------------------------------------------------
            # MAE-53: Semantic conflict detection
            # ---------------------------------------------------------------
            semantic = await _llm_is_real_conflict(fa, fb)
            is_conflict = semantic.get("is_conflict", True)
            confidence = semantic.get("confidence", 0.5)
            semantic_reason = semantic.get("reason", "")

            if confidence > 0.8 and not is_conflict:
                logger.info(
                    "False positive filtered: %s %s=%s vs %s — %s",
                    subject,
                    predicate,
                    fa["object"],
                    fb["object"],
                    semantic_reason,
                )
                continue  # Skip this pair — not a real conflict

            uncertain = confidence <= 0.8 and is_conflict

            # ---------------------------------------------------------------
            # Ask LLM which fact is correct
            # ---------------------------------------------------------------
            ai_id, ai_reason = await _llm_suggest(fa, fb)

            # ---------------------------------------------------------------
            # MAE-54: Auto-resolution scoring
            # ---------------------------------------------------------------
            score = await _llm_score_conflict(fa, fb)
            severity = score.get("severity", "MEDIUM")
            score_confidence = score.get("confidence", 0.0)
            auto_resolve = score.get("auto_resolve", False)
            winner_id = score.get("winner_id")

            # ---------------------------------------------------------------
            # MAE-55: Root cause analysis
            # ---------------------------------------------------------------
            root_cause = await _llm_root_cause(fa, fb)

            # ---------------------------------------------------------------
            # Determine auto-resolve logic
            # ---------------------------------------------------------------
            can_auto_resolve = (
                auto_resolve
                and score_confidence > 0.9
                and severity != "CRITICAL"
                and winner_id is not None
                and isinstance(winner_id, int)
            )

            # Factor in uncertainty from MAE-53
            if uncertain:
                can_auto_resolve = False
                if ai_reason:
                    ai_reason = "⚠️ Incertain — " + ai_reason

            # ---------------------------------------------------------------
            # Create the conflict in DB
            # ---------------------------------------------------------------
            conflict = self.store.create(
                subject,
                predicate,
                fa["id"],
                fb["id"],
                fa["source_id"],
                fb["source_id"],
                ai_fact_id=ai_id,
                ai_reason=ai_reason,
                semantic_confidence=confidence,
                semantic_reason=semantic_reason,
                severity=severity,
                score_confidence=score_confidence,
                root_cause=root_cause.get("root_cause", ""),
                truth_source=root_cause.get("truth_source", ""),
                suggested_fix=root_cause.get("suggested_fix", ""),
                fix_file=root_cause.get("fix_file", ""),
            )

            auto_resolved_flag = False

            # ---------------------------------------------------------------
            # Auto-resolve if applicable
            # ---------------------------------------------------------------
            if can_auto_resolve:
                assert isinstance(winner_id, int), "winner_id must be int at this point"
                self.store.mark_auto_resolved(
                    conflict["conflict_id"],
                    winner_id,
                    "✅ Auto-resolved by AI scoring",
                )
                auto_resolved_flag = True

            # ---------------------------------------------------------------
            # Build rich message
            # ---------------------------------------------------------------
            ai_label = "A" if ai_id == fa["id"] else "B"

            msg_text = _build_conflict_message(
                conflict=conflict,
                fact_a=fa,
                fact_b=fb,
                ai_label=ai_label,
                ai_reason=ai_reason,
                score=score,
                root_cause=root_cause,
                auto_resolved=auto_resolved_flag,
            )

            if uncertain:
                msg_text += "\n⚠️ Détection incertaine (confiance ≤ 0.8)"

            created.append(conflict)
            messages.append(msg_text)

        if not created:
            if skipped_existing:
                await tools.send_message(
                    "✅ No new conflicts. "
                    f"{skipped_existing} conflict pair(s) already tracked/resolved."
                )
            else:
                await tools.send_message(
                    "✅ All candidate pairs were semantically compatible (no real conflicts)."
                )
            return

        header = f"⚠️ {len(created)} conflict(s) detected:\n"
        await tools.send_message(header + "\n---\n".join(messages))

    async def _cmd_status(self, tools: AgentToolsProtocol) -> None:
        open_c = self.store.get_open()
        all_c = self.store.get_all()

        lines = [f"📊 {len(open_c)} open / {len(all_c)} total conflicts"]

        if open_c:
            lines.append("\n**Open:**")
            for c in open_c:
                severity_tag = ""
                if c.get("severity"):
                    severity_tag = f" [{c['severity']}]"
                ai = ""
                if c.get("ai_suggested_fact_id"):
                    ai = f" 💡 AI says fact #{c['ai_suggested_fact_id']}"
                conf = ""
                if c.get("score_confidence") is not None:
                    conf = f" (conf: {c['score_confidence']:.2f})"
                lines.append(
                    f"  `{c['conflict_id']}`{severity_tag} "
                    f"{c['subject']} ({c['predicate']}){ai}{conf}"
                )

        resolved = [c for c in all_c if c["status"] == "resolved"]
        if resolved:
            lines.append("\n**Resolved:**")
            for c in resolved[:5]:
                auto_tag = " 🤖" if c.get("auto_resolved") else ""
                lines.append(
                    f"  `{c['conflict_id']}`{auto_tag} → fact #{c['resolution_fact_id']} "
                    f"({c['resolution_reason'] or 'no reason'})"
                )

        await tools.send_message("\n".join(lines))

    async def _cmd_clear(self, tools: AgentToolsProtocol) -> None:
        count = self.store.clear()
        await tools.send_message(f"🗑️ Cleared {count} recorded conflict(s)")

    async def _cmd_resolve_all(
        self,
        args: str,
        tools: AgentToolsProtocol,
    ) -> None:
        parts = args.split()
        if len(parts) > 1:
            await tools.send_message("⚠️ Usage: `resolve-all [ai]`")
            return

        strategy = parts[0].lower() if parts else "ai"
        try:
            result = self.store.resolve_all(strategy)
        except ValueError as exc:
            await tools.send_message(f"❌ {exc}")
            return

        lines = [
            f"✅ Bulk resolution completed (`{result['strategy']}`)",
            "",
            f"Open conflicts: {result['total_open']}",
            f"Resolved: {result['resolved']}",
            f"Skipped: {result['skipped']}",
            f"Failed: {result['failed']}",
        ]

        if result["details"]:
            lines.append("")
            lines.append("Details:")
            for detail in result["details"]:
                label = (
                    f"`{detail['conflict_id']}` "
                    f"{detail['subject']} ({detail['predicate']})"
                )
                if detail["outcome"] == "resolved":
                    lines.append(f"- ✅ {label} → fact #{detail['fact_id']}")
                elif detail["outcome"] == "skipped":
                    lines.append(f"- ⏭️ {label}: {detail['reason']}")
                else:
                    lines.append(f"- ❌ {label}: {detail['reason']}")
        else:
            lines.extend(["", "No open conflicts to resolve."])

        await tools.send_message("\n".join(lines))

    async def _cmd_resolve(self, args: str, tools: AgentToolsProtocol) -> None:
        parts = args.strip().split(None, 2)
        if len(parts) < 2:
            await tools.send_message("⚠️ Usage: `resolve <conflict_id> <fact_id> [reason]`")
            return

        conflict_id = parts[0]
        try:
            fact_id = int(parts[1])
        except ValueError:
            await tools.send_message(f"⚠️ fact_id must be a number, got: {parts[1]}")
            return

        reason = parts[2] if len(parts) > 2 else "resolved via Reconciler"
        result = self.store.resolve(conflict_id, fact_id, reason)
        await tools.send_message(
            f"✅ Conflict `{result['conflict_id']}` resolved → fact #{fact_id}"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import os
    ReconcilerAgent(
        agent_id=os.getenv("BAND_RECONCILER_ID", ""),
        api_key=os.getenv("BAND_RECONCILER_KEY", ""),
    ).run()
