"""Reconciler Agent — Band-native conflict resolver with LLM suggestions.

Listens for conflict reports from Keeper, creates Band rooms,
@mentions agents, suggests a winner via LLM, and records resolutions.

Commands:
  @reconciler detect          → scan Keeper DB, LLM suggests winner
  @reconciler status          → show open/closed conflicts with AI suggestions
  @reconciler resolve <id> <fact_id> [reason]  → record resolution
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage

from agents.band_agent import BandAgent

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "reconciler.db"


# ---------------------------------------------------------------------------
# LLM helpers (async)
# ---------------------------------------------------------------------------


def _parse_llm_json(content: str) -> dict | None:
    """Parse LLM output — handles markdown fences, trailing commas, truncation."""
    if not content:
        return None
    cleaned = content.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        import re
        fixed = re.sub(r",\s*}", "}", cleaned)
        fixed = re.sub(r",\s*\]", "]", fixed)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None


async def _llm_call(
    system: str,
    user: str,
    max_tokens: int = 300,
) -> dict | None:
    """Low-level LLM call via httpx.AsyncClient.

    Provider chain: Featherless → OpenAI → returns None.
    Returns parsed JSON dict or None.
    """
    featherless_key = os.getenv("FEATHERLESS_API_KEY") or os.getenv("FEATHERLESS_KEY")
    featherless_model = os.getenv("FEATHERLESS_MODEL", "Qwen/Qwen2.5-14B-Instruct")
    openai_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None

    if featherless_key:
        api_key = featherless_key
        base_url = "https://api.featherless.ai/v1/chat/completions"
        model = featherless_model
    elif openai_key:
        api_key = openai_key
        base_url = "https://api.openai.com/v1/chat/completions"
        model = openai_model

    if not api_key or not base_url:
        return None

    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    base_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0.2,
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("LLM returned 0 choices")
                raw = choices[0].get("message", {}).get("content", "")
                parsed = _parse_llm_json(raw)
                if parsed is None:
                    raise ValueError(f"Unparseable JSON: {raw[:200]}")
                return parsed
        except Exception as e:
            logger.warning("LLM attempt %d/2 failed: %s", attempt, e)
            if attempt < 2:
                import asyncio
                await asyncio.sleep(1.5)

    return None


async def _llm_suggest(a: dict, b: dict) -> tuple[int, str]:
    """Ask LLM which fact is correct. Returns (winner_fact_id, reason).

    Provider chain: Featherless → OpenAI → timestamp fallback.
    """
    featherless_key = os.getenv("FEATHERLESS_API_KEY") or os.getenv("FEATHERLESS_KEY")
    featherless_model = os.getenv("FEATHERLESS_MODEL", "Qwen/Qwen2.5-14B-Instruct")
    openai_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None

    if featherless_key:
        api_key = featherless_key
        base_url = "https://api.featherless.ai/v1/chat/completions"
        model = featherless_model
    elif openai_key:
        api_key = openai_key
        base_url = "https://api.openai.com/v1/chat/completions"
        model = openai_model

    if not api_key:
        # Fallback: most recent fact wins
        winner = a if a["timestamp"] >= b["timestamp"] else b
        return winner["id"], "[Fallback] Most recent fact by timestamp (no LLM configured)."

    system = (
        "You are an expert data reconciliation agent. "
        "Compare two conflicting facts and respond with JSON containing:\n"
        '- "winner_id": integer (ID of the correct fact)\n'
        '- "reason": string (concise explanation)\n\n'
        "Return ONLY raw JSON — no markdown, no code fences."
    )

    user = (
        f"Compare these conflicting facts:\n\n"
        f"Fact A (ID={a['id']}): {a['subject']} → {a['predicate']} = {a['object']}\n"
        f"  Source: {a['source_id']} | Timestamp: {datetime.fromtimestamp(a['timestamp']).isoformat()}\n\n"
        f"Fact B (ID={b['id']}): {b['subject']} → {b['predicate']} = {b['object']}\n"
        f"  Source: {b['source_id']} | Timestamp: {datetime.fromtimestamp(b['timestamp']).isoformat()}"
    )

    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    base_url,  # type: ignore[arg-type]
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 300,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("LLM returned 0 choices")
                raw = choices[0].get("message", {}).get("content", "")
                parsed = _parse_llm_json(raw)
                if parsed is None:
                    raise ValueError(f"Unparseable JSON: {raw[:200]}")
                return int(parsed["winner_id"]), str(parsed.get("reason", ""))
        except Exception as e:
            logger.warning("LLM attempt %d/2 failed: %s", attempt, e)
            if attempt < 2:
                import asyncio
                await asyncio.sleep(1.5)

    # All retries exhausted — timestamp fallback
    winner = a if a["timestamp"] >= b["timestamp"] else b
    return winner["id"], "[Fallback] LLM unavailable. Recommended most recent by timestamp."


# ---------------------------------------------------------------------------
# MAE-53 — Semantic conflict detection via LLM
# ---------------------------------------------------------------------------


async def _llm_is_real_conflict(fact_a: dict, fact_b: dict) -> dict:
    """Ask LLM whether two facts are in real contradiction.

    Returns: {"is_conflict": bool, "confidence": float, "reason": str}
    Falls back to is_conflict=true with confidence=0.5 if LLM unavailable.
    """
    system = (
        "You are a semantic conflict detector. "
        "Compare two facts about the same subject and predicate. "
        "Determine whether they are in REAL contradiction or are compatible. "
        "Return JSON with: is_conflict (bool), confidence (0.0-1.0), reason (str).\n\n"
        "Examples:\n"
        "- 'Python >=3.10' vs 'Python 3.11+' → compatible, NOT a conflict\n"
        "- 'Framework: FastAPI' vs 'Framework: Django' → REAL conflict\n"
        "- 'port: 8080' vs 'port: 3000' → REAL conflict (different values for same config)\n"
        "- 'version: 1.0' vs 'version: 2.0' → REAL conflict\n"
        "- 'license: MIT' vs 'license: Apache-2.0' → REAL conflict\n\n"
        "Return ONLY raw JSON — no markdown, no code fences."
    )

    user = (
        f"Fact A: {fact_a['subject']} → {fact_a['predicate']} = {fact_a['object']}\n"
        f"  Source: {fact_a['source_id']}\n\n"
        f"Fact B: {fact_b['subject']} → {fact_b['predicate']} = {fact_b['object']}\n"
        f"  Source: {fact_b['source_id']}"
    )

    result = await _llm_call(system, user, max_tokens=200)
    if result is None:
        return {"is_conflict": True, "confidence": 0.5, "reason": "LLM unavailable — assuming conflict."}

    return {
        "is_conflict": bool(result.get("is_conflict", True)),
        "confidence": float(result.get("confidence", 0.5)),
        "reason": str(result.get("reason", "")),
    }


# ---------------------------------------------------------------------------
# MAE-54 — Auto-resolution scoring
# ---------------------------------------------------------------------------


async def _llm_score_conflict(fact_a: dict, fact_b: dict) -> dict:
    """Ask LLM to score a conflict and decide if it can be auto-resolved.

    Returns: {
        "severity": "CRITICAL|HIGH|MEDIUM|LOW",
        "confidence": 0.95,
        "auto_resolve": true,
        "winner_id": 42,
        "reason": "..."
    }
    Falls back to MEDIUM / no auto-resolve if LLM unavailable.
    """
    system = (
        "You are a conflict scoring AI. Given two conflicting facts, assess:\n"
        "- severity: CRITICAL (breaking changes, security), HIGH, MEDIUM, LOW\n"
        "- confidence: how sure you are (0.0-1.0)\n"
        "- auto_resolve: true if the conflict can be safely auto-resolved\n"
        "- winner_id: which fact ID is most likely correct\n"
        "- reason: brief explanation\n\n"
        "Return JSON with: severity, confidence, auto_resolve, winner_id, reason.\n"
        "Return ONLY raw JSON — no markdown, no code fences."
    )

    user = (
        f"Fact A (ID={fact_a['id']}): {fact_a['subject']} → {fact_a['predicate']} = {fact_a['object']}\n"
        f"  Source: {fact_a['source_id']} | Timestamp: {datetime.fromtimestamp(fact_a['timestamp']).isoformat()}\n\n"
        f"Fact B (ID={fact_b['id']}): {fact_b['subject']} → {fact_b['predicate']} = {fact_b['object']}\n"
        f"  Source: {fact_b['source_id']} | Timestamp: {datetime.fromtimestamp(fact_b['timestamp']).isoformat()}"
    )

    result = await _llm_call(system, user, max_tokens=300)
    if result is None:
        return {
            "severity": "MEDIUM",
            "confidence": 0.0,
            "auto_resolve": False,
            "winner_id": None,
            "reason": "LLM unavailable — escalated to human.",
        }

    return {
        "severity": str(result.get("severity", "MEDIUM")),
        "confidence": float(result.get("confidence", 0.0)),
        "auto_resolve": bool(result.get("auto_resolve", False)),
        "winner_id": result.get("winner_id"),
        "reason": str(result.get("reason", "")),
    }


# ---------------------------------------------------------------------------
# MAE-55 — Root cause + suggested fix
# ---------------------------------------------------------------------------


async def _llm_root_cause(
    fact_a: dict,
    fact_b: dict,
    file_a_content: str = "",
    file_b_content: str = "",
) -> dict:
    """Ask LLM to explain WHY a conflict exists and propose a fix.

    Returns: {
        "root_cause": "...",
        "truth_source": "...",
        "suggested_fix": "...",
        "fix_file": "...",
    }
    Falls back to empty strings if LLM unavailable.
    """
    system = (
        "You are a root cause analyst for data conflicts. "
        "Given two conflicting facts, explain:\n"
        "- root_cause: why this conflict exists (e.g., 'Outdated docs', 'Migration drift')\n"
        "- truth_source: which source should be trusted and why\n"
        "- suggested_fix: a concrete diff-like fix suggestion\n"
        "- fix_file: which file needs updating\n\n"
        "Return JSON with: root_cause, truth_source, suggested_fix, fix_file.\n"
        "Return ONLY raw JSON — no markdown, no code fences."
    )

    file_section = ""
    if file_a_content:
        file_section += f"\nFile A content:\n{file_a_content[:500]}\n"
    if file_b_content:
        file_section += f"\nFile B content:\n{file_b_content[:500]}\n"

    user = (
        f"Fact A (ID={fact_a['id']}): {fact_a['subject']} → {fact_a['predicate']} = {fact_a['object']}\n"
        f"  Source: {fact_a['source_id']}\n\n"
        f"Fact B (ID={fact_b['id']}): {fact_b['subject']} → {fact_b['predicate']} = {fact_b['object']}\n"
        f"  Source: {fact_b['source_id']}"
        f"{file_section}"
    )

    result = await _llm_call(system, user, max_tokens=400)
    if result is None:
        return {
            "root_cause": "",
            "truth_source": "",
            "suggested_fix": "",
            "fix_file": "",
        }

    return {
        "root_cause": str(result.get("root_cause", "")),
        "truth_source": str(result.get("truth_source", "")),
        "suggested_fix": str(result.get("suggested_fix", "")),
        "fix_file": str(result.get("fix_file", "")),
    }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ReconcilerStore:
    """SQLite store for conflicts with AI suggestion columns."""

    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conflicts (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                fact_a_id INTEGER NOT NULL,
                fact_b_id INTEGER NOT NULL,
                source_a TEXT NOT NULL,
                source_b TEXT NOT NULL,
                band_room_id TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                resolution_fact_id INTEGER,
                resolution_reason TEXT,
                created_at INTEGER NOT NULL,
                resolved_at INTEGER,
                ai_suggested_fact_id INTEGER,
                ai_reason TEXT,
                -- MAE-53: semantic conflict metadata
                semantic_confidence REAL,
                semantic_reason TEXT,
                -- MAE-54: auto-resolution scoring
                severity TEXT,
                score_confidence REAL,
                auto_resolved INTEGER DEFAULT 0,
                -- MAE-55: root cause analysis
                root_cause TEXT,
                truth_source TEXT,
                suggested_fix TEXT,
                fix_file TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)"
        )
        self.conn.commit()

    def migrate_schema(self) -> None:
        """Add columns missing in older schema versions (idempotent)."""
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(conflicts)").fetchall()}
        additions = {
            "semantic_confidence": "REAL",
            "semantic_reason": "TEXT",
            "severity": "TEXT",
            "score_confidence": "REAL",
            "auto_resolved": "INTEGER DEFAULT 0",
            "root_cause": "TEXT",
            "truth_source": "TEXT",
            "suggested_fix": "TEXT",
            "fix_file": "TEXT",
        }
        for col, coltype in additions.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE conflicts ADD COLUMN {col} {coltype}")
        self.conn.commit()

    def create(self, subject: str, predicate: str,
               fact_a_id: int, fact_b_id: int,
               source_a: str, source_b: str,
               ai_fact_id: int | None = None,
               ai_reason: str | None = None,
               semantic_confidence: float | None = None,
               semantic_reason: str | None = None,
               severity: str | None = None,
               score_confidence: float | None = None,
               root_cause: str | None = None,
               truth_source: str | None = None,
               suggested_fix: str | None = None,
               fix_file: str | None = None) -> dict:
        conflict_id = str(uuid.uuid4())[:8]
        ts = int(time.time())
        self.conn.execute(
            "INSERT INTO conflicts (id, subject, predicate, fact_a_id, fact_b_id, "
            "source_a, source_b, created_at, ai_suggested_fact_id, ai_reason, "
            "semantic_confidence, semantic_reason, severity, score_confidence, "
            "root_cause, truth_source, suggested_fix, fix_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conflict_id, subject, predicate, fact_a_id, fact_b_id,
             source_a, source_b, ts, ai_fact_id, ai_reason,
             semantic_confidence, semantic_reason, severity, score_confidence,
             root_cause, truth_source, suggested_fix, fix_file),
        )
        self.conn.commit()
        return {"conflict_id": conflict_id, "status": "open",
                "ai_suggested_fact_id": ai_fact_id, "ai_reason": ai_reason,
                "severity": severity, "score_confidence": score_confidence,
                "semantic_confidence": semantic_confidence}

    def mark_auto_resolved(self, conflict_id: str, winner_fact_id: int, reason: str) -> dict:
        ts = int(time.time())
        self.conn.execute(
            "UPDATE conflicts SET status='resolved', resolution_fact_id=?, "
            "resolution_reason=?, resolved_at=?, auto_resolved=1 WHERE id=?",
            (winner_fact_id, reason, ts, conflict_id),
        )
        self.conn.commit()
        return {"conflict_id": conflict_id, "status": "resolved", "auto_resolved": True}

    def resolve(self, conflict_id: str, fact_id: int, reason: str) -> dict:
        ts = int(time.time())
        self.conn.execute(
            "UPDATE conflicts SET status='resolved', resolution_fact_id=?, "
            "resolution_reason=?, resolved_at=? WHERE id=?",
            (fact_id, reason, ts, conflict_id),
        )
        self.conn.commit()
        return {"conflict_id": conflict_id, "status": "resolved"}

    def get_open(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, fact_a_id, fact_b_id, source_a, source_b, "
            "band_room_id, created_at, ai_suggested_fact_id, ai_reason, "
            "semantic_confidence, severity, score_confidence, auto_resolved "
            "FROM conflicts WHERE status='open' ORDER BY created_at DESC"
        ).fetchall()
        keys = ["conflict_id", "subject", "predicate", "fact_a_id", "fact_b_id",
                "source_a", "source_b", "band_room_id", "created_at",
                "ai_suggested_fact_id", "ai_reason", "semantic_confidence",
                "severity", "score_confidence", "auto_resolved"]
        return [dict(zip(keys, r)) for r in rows]

    def get_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, status, created_at, resolved_at, "
            "resolution_fact_id, resolution_reason, ai_suggested_fact_id, ai_reason, "
            "severity, score_confidence, auto_resolved, root_cause, truth_source, "
            "suggested_fix, fix_file "
            "FROM conflicts ORDER BY created_at DESC"
        ).fetchall()
        keys = ["conflict_id", "subject", "predicate", "status", "created_at",
                "resolved_at", "resolution_fact_id", "resolution_reason",
                "ai_suggested_fact_id", "ai_reason", "severity",
                "score_confidence", "auto_resolved", "root_cause",
                "truth_source", "suggested_fix", "fix_file"]
        return [dict(zip(keys, r)) for r in rows]

    def get_fact_row(self, subject: str, predicate: str) -> list[dict]:
        """Get all facts matching a subject/predicate from keeper.db."""
        # This is called externally with a keeper_db connection
        return []

    def close(self) -> None:
        self.conn.close()


def _build_conflict_message(
    conflict: dict,
    fact_a: dict,
    fact_b: dict,
    ai_label: str,
    ai_reason: str,
    score: dict | None = None,
    root_cause: dict | None = None,
    auto_resolved: bool = False,
) -> str:
    """Build a rich conflict message following the MAE-55 spec format."""
    lines = [f"⚠️ CONFLIT #{conflict['conflict_id']}: {fact_a['predicate']} ({fact_a['subject']})"]

    # Basic fact display
    lines.append(f"  Fact A: {fact_a['object']} ({fact_a['source_id']})")
    lines.append(f"  Fact B: {fact_b['object']} ({fact_b['source_id']})")

    # MAE-54: Score
    if score:
        severity = score.get("severity", "MEDIUM")
        conf = score.get("confidence", 0.0)
        lines.append(f"📊 Score: {severity} | confiance: {conf:.2f}")

    # LLM suggestion
    if ai_label:
        lines.append(f"💡 AI suggère Fact {ai_label} (#{conflict.get('ai_suggested_fact_id', '?')})")
        if ai_reason:
            lines.append(f"   Raison: {ai_reason[:200]}")

    # MAE-55: Root cause + fix
    if root_cause:
        rc = root_cause.get("root_cause", "")
        if rc:
            lines.append(f"🔍 Root cause: {rc}")
        ts = root_cause.get("truth_source", "")
        if ts:
            lines.append(f"💡 Source de vérité: {ts}")
        sf = root_cause.get("suggested_fix", "")
        if sf:
            lines.append(f"🛠 Correctif proposé: {sf[:200]}")
        ff = root_cause.get("fix_file", "")
        if ff:
            lines.append(f"📄 Fichier: {ff}")

    # Auto-resolution indicator
    if auto_resolved:
        lines.append("🤖 Auto-resolved ✅")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ReconcilerAgent(BandAgent):
    agent_name = "Reconciler"
    agent_description = "Conflict resolver with AI suggestions. Commands: detect, status, resolve"

    def __init__(self, keeper_db: str = "") -> None:
        super().__init__()
        self.store = ReconcilerStore()
        self.store.migrate_schema()
        self.keeper_db_path = keeper_db or str(
            Path(__file__).parent.parent / "data" / "keeper.db"
        )

    async def handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> None:
        content = msg.content.strip()

        if content.startswith("detect"):
            await self._cmd_detect(tools)
            return

        if content == "status":
            await self._cmd_status(tools)
            return

        if content.startswith("resolve "):
            await self._cmd_resolve(content[8:], tools)
            return

        await tools.send_message(
            "🤖 Reconciler commands:\n"
            "  `detect`            → scan Keeper DB, AI suggests winner\n"
            "  `status`            → show open/closed conflicts\n"
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

        for r in rows:
            subject, predicate = r[0], r[1]
            fa = {"id": r[2], "object": r[3], "source_id": r[4], "timestamp": r[5],
                  "subject": subject, "predicate": predicate}
            fb = {"id": r[6], "object": r[7], "source_id": r[8], "timestamp": r[9],
                  "subject": subject, "predicate": predicate}

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
                    subject, predicate, fa["object"], fb["object"], semantic_reason,
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
                subject, predicate, fa["id"], fb["id"],
                fa["source_id"], fb["source_id"],
                ai_fact_id=ai_id, ai_reason=ai_reason,
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
            await tools.send_message("✅ All candidate pairs were semantically compatible (no real conflicts).")
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
                lines.append(f"  `{c['conflict_id']}`{severity_tag} {c['subject']} ({c['predicate']}){ai}{conf}")

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
        await tools.send_message(f"✅ Conflict `{result['conflict_id']}` resolved → fact #{fact_id}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ReconcilerAgent().run()
