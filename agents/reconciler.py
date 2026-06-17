"""Reconciler Agent — detect contradictions via SQL JOIN, resolve via LLM + Band.

Conflict detection no longer loads all facts into memory.  Instead it
calls Keeper's ``detect-conflicts`` RPC which runs an optimised SQL JOIN.

Supports:
- SQL-based conflict detection (Keeper-side JOIN, O(n log n))
- LLM suggestion (Featherless → OpenAI → timestamp fallback) with resilient JSON parsing
- Band room creation with retry + structured logging
- Band webhook receiver for push-based resolution
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from agents.auth import a2a_call
from agents.base import Agent
from agents.provider import provider
from agents.validation import DetectConflictParams, ResolveParams
from protocols.a2a import AgentCard, A2AResponse, INVALID_PARAMS

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)


DB_PATH = Path(__file__).parent.parent / "data" / "reconciler.db"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ReconcilerStore:
    """SQLite store for conflicts with AI suggestion columns."""

    def __init__(self, db_path: str = str(DB_PATH)) -> None:
        import sqlite3

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys=ON")
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
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_conflicts_subject ON conflicts(subject)")
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

    def create(
        self,
        subject: str,
        predicate: str,
        fact_a_id: int,
        fact_b_id: int,
        source_a: str,
        source_b: str,
        ai_fact_id: int | None = None,
        ai_reason: str | None = None,
        semantic_confidence: float | None = None,
        semantic_reason: str | None = None,
        severity: str | None = None,
        score_confidence: float | None = None,
        root_cause: str | None = None,
        truth_source: str | None = None,
        suggested_fix: str | None = None,
        fix_file: str | None = None,
    ) -> dict:
        conflict_id = str(uuid.uuid4())[:8]
        ts = int(time.time())
        self.conn.execute(
            "INSERT INTO conflicts (id, subject, predicate, fact_a_id, fact_b_id, "
            "source_a, source_b, created_at, ai_suggested_fact_id, ai_reason, "
            "semantic_confidence, semantic_reason, severity, score_confidence, "
            "root_cause, truth_source, suggested_fix, fix_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conflict_id,
                subject,
                predicate,
                fact_a_id,
                fact_b_id,
                source_a,
                source_b,
                ts,
                ai_fact_id,
                ai_reason,
                semantic_confidence,
                semantic_reason,
                severity,
                score_confidence,
                root_cause,
                truth_source,
                suggested_fix,
                fix_file,
            ),
        )
        self.conn.commit()
        return {
            "conflict_id": conflict_id,
            "status": "open",
            "subject": subject,
            "predicate": predicate,
            "ai_suggested_fact_id": ai_fact_id,
            "ai_reason": ai_reason,
            "severity": severity,
            "score_confidence": score_confidence,
            "semantic_confidence": semantic_confidence,
        }

    def create_conflict(
        self,
        subject: str,
        predicate: str,
        fact_a_id: int,
        fact_b_id: int,
        source_a: str,
        source_b: str,
        ai_suggested_fact_id: int | None = None,
        ai_reason: str | None = None,
        **kwargs,
    ) -> dict:
        return self.create(
            subject=subject,
            predicate=predicate,
            fact_a_id=fact_a_id,
            fact_b_id=fact_b_id,
            source_a=source_a,
            source_b=source_b,
            ai_fact_id=ai_suggested_fact_id,
            ai_reason=ai_reason,
            **kwargs,
        )

    def get_conflict_for_pair(self, fact_a_id: int, fact_b_id: int) -> dict | None:
        row = self.conn.execute(
            """
            SELECT id, subject, predicate, status
            FROM conflicts
            WHERE
                (fact_a_id = ? AND fact_b_id = ?)
                OR
                (fact_a_id = ? AND fact_b_id = ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (fact_a_id, fact_b_id, fact_b_id, fact_a_id),
        ).fetchone()

        if not row:
            return None

        return {
            "conflict_id": row[0],
            "subject": row[1],
            "predicate": row[2],
            "status": row[3],
        }

    def mark_auto_resolved(self, conflict_id: str, winner_fact_id: int, reason: str) -> dict:
        ts = int(time.time())
        self.conn.execute(
            "UPDATE conflicts SET status='resolved', resolution_fact_id=?, "
            "resolution_reason=?, resolved_at=?, auto_resolved=1 WHERE id=?",
            (winner_fact_id, reason, ts, conflict_id),
        )
        self.conn.commit()
        return {"conflict_id": conflict_id, "status": "resolved", "auto_resolved": True}

    def resolve(self, conflict_id: str, resolution_fact_id: int, reason: str) -> dict:
        ts = int(time.time())
        self.conn.execute(
            "UPDATE conflicts SET status='resolved', resolution_fact_id=?, "
            "resolution_reason=?, resolved_at=? WHERE id=?",
            (resolution_fact_id, reason, ts, conflict_id),
        )
        self.conn.commit()
        logger.info("Conflict %s resolved → fact %d", conflict_id, resolution_fact_id)
        return {"conflict_id": conflict_id, "status": "resolved"}

    def get_open(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, fact_a_id, fact_b_id, source_a, source_b, "
            "band_room_id, created_at, ai_suggested_fact_id, ai_reason, "
            "semantic_confidence, severity, score_confidence, auto_resolved "
            "FROM conflicts WHERE status='open' ORDER BY created_at DESC"
        ).fetchall()
        keys = [
            "conflict_id",
            "subject",
            "predicate",
            "fact_a_id",
            "fact_b_id",
            "source_a",
            "source_b",
            "band_room_id",
            "created_at",
            "ai_suggested_fact_id",
            "ai_reason",
            "semantic_confidence",
            "severity",
            "score_confidence",
            "auto_resolved",
        ]
        return [dict(zip(keys, r)) for r in rows]

    def get_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, status, created_at, resolved_at, "
            "resolution_fact_id, resolution_reason, ai_suggested_fact_id, ai_reason, "
            "severity, score_confidence, auto_resolved, root_cause, truth_source, "
            "suggested_fix, fix_file "
            "FROM conflicts ORDER BY created_at DESC"
        ).fetchall()
        keys = [
            "conflict_id",
            "subject",
            "predicate",
            "status",
            "created_at",
            "resolved_at",
            "resolution_fact_id",
            "resolution_reason",
            "ai_suggested_fact_id",
            "ai_reason",
            "severity",
            "score_confidence",
            "auto_resolved",
            "root_cause",
            "truth_source",
            "suggested_fix",
            "fix_file",
        ]
        return [dict(zip(keys, r)) for r in rows]

    def clear(self) -> int:
        """Delete all recorded conflicts. Returns count of deleted rows."""
        count = self.conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
        self.conn.execute("DELETE FROM conflicts")
        self.conn.commit()
        return count

    def get_fact_row(self, subject: str, predicate: str) -> list[dict]:
        return []

    def set_band_room(self, conflict_id: str, room_id: str) -> None:
        self.conn.execute(
            "UPDATE conflicts SET band_room_id=? WHERE id=?",
            (room_id, conflict_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Band Client with retries + logging
# ---------------------------------------------------------------------------

BAND_API_BASE = "https://app.band.ai/api/v1"


class BandError(Exception):
    """Raised when a Band API call fails after all retries."""


class BandClient:
    """Resilient Band REST API client with retry + structured logging."""

    def __init__(self, agent_id: str, api_key: str, *, max_retries: int = 3) -> None:
        if not api_key:
            raise ValueError("BAND_API_KEY must be set")
        self.agent_id = agent_id
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=BAND_API_BASE,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def create_room(self, title: str) -> dict:
        """Create a Band chat room. Raises BandError on failure."""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.post(
                    "/agent/chats",
                    json={
                        "chat": {
                            "title": title,
                        }
                    },
                )

                if resp.is_success:
                    data = resp.json()
                    room = data.get("data", data)
                    logger.info("Band chat created: %s (title=%r)", room.get("id"), title)
                    return room

                logger.warning(
                    "Band create_room attempt %d/%d: HTTP %d %s",
                    attempt,
                    self.max_retries,
                    resp.status_code,
                    resp.text[:300],
                )

                if resp.status_code in (429, 500, 502, 503):
                    await _exponential_backoff(attempt)
                    continue

                raise BandError(
                    f"Band create_room failed: HTTP {resp.status_code} – {resp.text[:300]}"
                )

            except httpx.TimeoutException:
                logger.warning(
                    "Band create_room timeout attempt %d/%d", attempt, self.max_retries
                )
                if attempt < self.max_retries:
                    await _exponential_backoff(attempt)
                    continue
                raise BandError(
                    f"Band create_room timeout after {self.max_retries} retries"
                ) from None

        raise BandError("Band create_room exhausted retries")

    async def post_message(self, room_id: str, message: str) -> dict:
        """Post a conflict event to a Band chat room."""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.post(
                    f"/agent/chats/{room_id}/events",
                    json={
                        "event": {
                            "content": message,
                            "message_type": "task",
                        }
                    },
                )

                if resp.is_success:
                    return resp.json()

                logger.warning(
                    "Band post_message attempt %d/%d: HTTP %d %s",
                    attempt,
                    self.max_retries,
                    resp.status_code,
                    resp.text[:300],
                )

                if resp.status_code in (429, 500, 502, 503):
                    await _exponential_backoff(attempt)
                    continue

                raise BandError(f"Band post_message HTTP {resp.status_code}: {resp.text[:300]}")

            except httpx.TimeoutException:
                logger.warning(
                    "Band post_message timeout attempt %d/%d", attempt, self.max_retries
                )
                if attempt < self.max_retries:
                    await _exponential_backoff(attempt)
                    continue
                raise BandError(
                    f"Band post_message timeout after {self.max_retries} retries"
                ) from None

        raise BandError("Band post_message exhausted retries")

    async def aclose(self) -> None:
        await self._client.aclose()


async def _exponential_backoff(attempt: int) -> None:
    import asyncio

    delay = min(0.5 * (2 ** (attempt - 1)), 5.0)
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# LLM Suggestion (async)
# ---------------------------------------------------------------------------




async def _llm_call(
    system: str,
    user: str,
    max_tokens: int = 300,
) -> dict | None:
    """Low-level LLM call via shared provider.

    Provider chain: Featherless → OpenAI → returns None.
    Returns parsed JSON dict or None.
    """
    result = await provider.chat_completion(
        system, user,
        temperature=0.2,
        max_tokens=max_tokens,
        parse_json=True,
    )
    if isinstance(result, dict):
        return result
    return None


async def _llm_suggest(a: dict, b: dict) -> tuple[int, str]:
    """Ask LLM which fact is correct. Returns (winner_fact_id, reason).

    Provider chain: Featherless → OpenAI → timestamp fallback.
    """
    from datetime import datetime as dt

    system = (
        "You are an expert AI data reconciliation agent. "
        "Compare two conflicting facts and respond with JSON containing:\n"
        '- "winner_id": integer (ID of the correct fact)\n'
        '- "reason": string (concise explanation)\n\n'
        "Return ONLY raw JSON — no markdown, no code fences."
    )

    user = (
        f"Compare these conflicting facts:\n\n"
        f"Fact A (ID={a['id']}): {a['subject']} → {a['predicate']} = {a['object']}\n"
        f"  Source: {a['source_id']} | Timestamp: {dt.fromtimestamp(a['timestamp']).isoformat()}\n\n"
        f"Fact B (ID={b['id']}): {b['subject']} → {b['predicate']} = {b['object']}\n"
        f"  Source: {b['source_id']} | Timestamp: {dt.fromtimestamp(b['timestamp']).isoformat()}"
    )

    result = await provider.chat_completion(
        system, user,
        temperature=0.2,
        max_tokens=300,
        parse_json=True,
    )

    if isinstance(result, dict) and "winner_id" in result:
        return int(result["winner_id"]), str(result.get("reason", ""))

    # Fallback: most recent fact wins
    winner = a if a["timestamp"] >= b["timestamp"] else b
    return winner["id"], "[Fallback Rule] LLM suggestion failed or unavailable, used most recent fact."


# Alias for reconciler.py internal usage
_get_ai_suggestion = _llm_suggest


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
        return {
            "is_conflict": True,
            "confidence": 0.5,
            "reason": "LLM unavailable — assuming conflict.",
        }

    return {
        "is_conflict": bool(result.get("is_conflict", True)),
        "confidence": float(result.get("confidence", 0.5)),
        "reason": str(result.get("reason", "")),
    }


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

    from datetime import datetime as dt

    user = (
        f"Fact A (ID={fact_a['id']}): {fact_a['subject']} → {fact_a['predicate']} = {fact_a['object']}\n"
        f"  Source: {fact_a['source_id']} | Timestamp: {dt.fromtimestamp(fact_a['timestamp']).isoformat()}\n\n"
        f"Fact B (ID={fact_b['id']}): {fact_b['subject']} → {fact_b['predicate']} = {fact_b['object']}\n"
        f"  Source: {fact_b['source_id']} | Timestamp: {dt.fromtimestamp(fact_b['timestamp']).isoformat()}"
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
    """Build a rich conflict message formatting."""
    lines = [f"⚠️ CONFLIT #{conflict['conflict_id']}: {fact_a['predicate']} ({fact_a['subject']})"]

    # Basic fact display
    lines.append(f"  Fact A: {fact_a['object']} ({fact_a['source_id']})")
    lines.append(f"  Fact B: {fact_b['object']} ({fact_b['source_id']})")

    # Score
    if score:
        severity = score.get("severity", "MEDIUM")
        conf = score.get("confidence", 0.0)
        lines.append(f"📊 Score: {severity} | confiance: {conf:.2f}")

    # LLM suggestion
    if ai_label:
        lines.append(
            f"💡 AI suggère Fact {ai_label} (#{conflict.get('ai_suggested_fact_id', '?')})"
        )
        if ai_reason:
            lines.append(f"   Raison: {ai_reason[:200]}")

    # Root cause + fix
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
# Reconciler Agent
# ---------------------------------------------------------------------------


class ReconcilerAgent(Agent):
    card = AgentCard(
        name="Reconciler Agent",
        description="Detects contradictory facts and resolves them via Band",
        url="http://localhost:8767",
        skills=["detect-conflict", "resolve", "status"],
        authentication={"schemes": [{"type": "bearer"}]},
    )
    port = 8767
    agent_role = "reconciler"

    def __init__(
        self,
        band_agent_id: str | None = None,
        band_api_key: str | None = None,
        *,
        keeper_url: str = "http://localhost:8766",
    ) -> None:
        super().__init__()
        self.store = ReconcilerStore()
        self.store.migrate_schema()
        self.connection = self.store.conn
        self.keeper_url = keeper_url
        self.band: BandClient | None = None
        if band_agent_id and band_api_key:
            try:
                self.band = BandClient(band_agent_id, band_api_key)
                logger.info("Band client initialised (agent_id=%s)", band_agent_id)
            except ValueError as e:
                logger.warning("Band client disabled: %s", e)

        # Register the webhook endpoint for Band push-based resolution
        from starlette.routing import Route

        self._starlette.routes.append(Route("/band-webhook", self.band_webhook, methods=["POST"]))
        self._starlette.routes.append(Route("/dashboard", self.dashboard_page, methods=["GET"]))
        self._starlette.routes.append(Route("/api/dashboard/data", self.dashboard_data, methods=["GET"]))
        self._starlette.routes.append(Route("/api/dashboard/resolve", self.dashboard_resolve, methods=["POST"]))

    # ------------------------------------------------------------------
    # RPC dispatcher
    # ------------------------------------------------------------------

    async def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        if method == "detect-conflict":
            return await self._detect(params)

        if method == "resolve":
            return await self._resolve(params)

        if method == "status":
            return {"open": self.store.get_open(), "all": self.store.get_all()}

        if method == "open-conflicts":
            return {"conflicts": self.store.get_open()}

        raise ValueError(f"unknown method: {method}")

    async def _detect(self, params: dict) -> dict:
        """Scan Keeper for contradictions using SQL JOIN (not O(n²))."""
        p = DetectConflictParams(**params)
        keeper_url = p.keeper_url or self.keeper_url

        try:
            result = await a2a_call(
                keeper_url,
                "detect-conflicts",
                {"limit": 500, "offset": 0},
                target_role="keeper",
                timeout=15.0,
            )
        except Exception as e:
            logger.error("Failed to detect conflicts from keeper %s: %s", keeper_url, e)
            return {"conflicts": [], "message": f"keeper unreachable: {e}"}

        raw_conflicts = result.get("conflicts", [])
        if not raw_conflicts:
            return {"conflicts": [], "message": "no conflicts found"}

        created: list[dict] = []
        for c in raw_conflicts:
            # Get AI suggestion (async)
            fact_a = {
                "id": c["fact_a_id"],
                "source_id": c["source_a"],
                "object": c["object_a"],
                "timestamp": c["timestamp_a"],
                "subject": c["subject"],
                "predicate": c["predicate"],
            }
            fact_b = {
                "id": c["fact_b_id"],
                "source_id": c["source_b"],
                "object": c["object_b"],
                "timestamp": c["timestamp_b"],
                "subject": c["subject"],
                "predicate": c["predicate"],
            }

            ai_id, ai_reason = await _get_ai_suggestion(fact_a, fact_b)

            conflict = self.store.create_conflict(
                subject=c["subject"],
                predicate=c["predicate"],
                fact_a_id=c["fact_a_id"],
                fact_b_id=c["fact_b_id"],
                source_a=c["source_a"],
                source_b=c["source_b"],
                ai_suggested_fact_id=ai_id,
                ai_reason=ai_reason,
            )
            created.append(conflict)

            # Post to Band if configured
            if self.band:
                try:
                    await self._notify_band(fact_a, fact_b, conflict)
                except Exception as e:
                    logger.error(
                        "Band notification failed for conflict %s: %s", conflict["conflict_id"], e
                    )

        return {"conflicts": created, "count": len(created)}

    async def _resolve(self, params: dict) -> dict:
        p = ResolveParams(**params)
        return self.store.resolve(
            conflict_id=p.conflict_id,
            resolution_fact_id=p.resolution_fact_id,
            reason=p.reason,
        )

    # ------------------------------------------------------------------
    # Band integration
    # ------------------------------------------------------------------

    async def _notify_band(self, a: dict, b: dict, conflict: dict) -> None:
        if not self.band:
            return

        title = f"Conflict: {a['subject']} ({a['predicate']})"
        try:
            room = await self.band.create_room(title)
        except BandError as e:
            logger.error("Cannot create Band room: %s", e)
            return

        room_id = room.get("id", "")
        self.store.set_band_room(conflict["conflict_id"], room_id)

        import asyncio

        # ── Act 1: Registry announces the discovery ──
        registry_msg = (
            "🔍 **[Registry Agent]** J'ai découvert le Keeper Agent (port 8766) "
            "et le Reconciler Agent (port 8767) pour traiter ce conflit.\n"
            f"_Conflit: {a['subject']} / {a['predicate']}_"
        )
        try:
            await self.band.post_message(room_id, registry_msg)
            await asyncio.sleep(0.3)
        except BandError as e:
            logger.error("Cannot post Registry message to room %s: %s", room_id, e)

        # ── Act 2: Keeper reports the contradiction ──
        keeper_msg = (
            f"💾 **[Keeper Agent]** Contradiction détectée en base :\n"
            f"- Fait A (ID={a['id']}) provenant de *{a['source_id']}* : "
            f"**{a['object']}**\n"
            f"- Fait B (ID={b['id']}) provenant de *{b['source_id']}* : "
            f"**{b['object']}**\n\n"
            f"_Transmission au Reconciler pour analyse IA..._"
        )
        try:
            await self.band.post_message(room_id, keeper_msg)
            await asyncio.sleep(0.3)
        except BandError as e:
            logger.error("Cannot post Keeper message to room %s: %s", room_id, e)

        # ── Act 3: Reconciler delivers the analysis ──
        ai_text = ""
        ai_id = conflict.get("ai_suggested_fact_id")
        if ai_id:
            winner_fact = a if ai_id == a["id"] else b
            ai_text = (
                f"\n**🤖 Recommandation IA:**\n"
                f"- Fait suggéré: Fact {ai_id} ({winner_fact['object']})\n"
                f"- Raison: {conflict.get('ai_reason', 'N/A')}\n"
            )
        else:
            ai_text = "\n**🤖 Recommandation IA:** Analyse non disponible (LLM non configuré)\n"

        reconciler_msg = (
            f"🧠 **[Reconciler Agent]** Analyse terminée.\n\n"
            f"**Conflit #{conflict['conflict_id']}**\n"
            f"- Sujet: {a['subject']}\n"
            f"- Prédicat: {a['predicate']}\n\n"
            f"{ai_text}\n"
            f"---\n"
            f"👤 **À l'humain :** répondez naturellement pour résoudre.\n"
            f"Ex: \"Prends le fait 2\", \"OK pour {b['object']}\", \"Version {a['source_id']}\"\n"
            f"Ou CLI: `mesh resolve {conflict['conflict_id']} <fact_id>`"
        )
        try:
            await self.band.post_message(room_id, reconciler_msg)
        except BandError as e:
            logger.error("Cannot post Reconciler message to room %s: %s", room_id, e)

    # ------------------------------------------------------------------
    # Web Dashboard Endpoints
    # ------------------------------------------------------------------

    async def dashboard_page(self, request: Request) -> HTMLResponse:
        """Serve the visual interactive Web Dashboard."""
        from starlette.responses import HTMLResponse
        return HTMLResponse(DASHBOARD_HTML)

    async def dashboard_data(self, request: Request) -> JSONResponse:
        """Provide dynamic facts, conflicts, and registry info for the dashboard."""
        from starlette.responses import JSONResponse
        from agents.auth import a2a_call
        
        conflicts = self.store.get_all()
        
        try:
            facts_res = await a2a_call(
                self.keeper_url,
                "list-facts",
                {"limit": 1000},
                target_role="keeper",
                timeout=10.0
            )
            facts = facts_res.get("facts", [])
        except Exception as e:
            logger.error("Dashboard failed to retrieve facts from Keeper: %s", e)
            facts = []

        try:
            registry_res = await a2a_call(
                "http://localhost:8765",
                "list",
                {},
                target_role="registry",
                timeout=10.0
            )
            registry_agents = registry_res.get("agents", [])
        except Exception as e:
            logger.error("Dashboard failed to retrieve agents from Registry: %s", e)
            registry_agents = []

        return JSONResponse({
            "conflicts": conflicts,
            "facts": facts,
            "registry_agents": registry_agents,
            "ports": {
                "registry": 8765,
                "keeper": 8766,
                "reconciler": 8767
            }
        })

    async def dashboard_resolve(self, request: Request) -> JSONResponse:
        """Resolve a conflict immediately from the dashboard interface."""
        from starlette.responses import JSONResponse
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        conflict_id = body.get("conflict_id")
        winner_fact_id = body.get("winner_fact_id")
        reason = body.get("reason", "Resolved manually via Web Dashboard")

        if not conflict_id or winner_fact_id is None:
            return JSONResponse({"error": "conflict_id and winner_fact_id are required"}, status_code=422)

        try:
            result = self.store.resolve(conflict_id, int(winner_fact_id), reason=reason)
            return JSONResponse({"status": "success", "result": result})
        except Exception as e:
            logger.error("Failed to resolve conflict from dashboard: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ------------------------------------------------------------------
    # Band Webhook — push-based resolution
    # ------------------------------------------------------------------

    async def band_webhook(self, request: Request) -> JSONResponse:
        """Receive a webhook from Band when a user replies in a room.

        Uses LLM (via ``provider.chat_completion``) to understand natural
        language resolution intent.  Falls back to regex when no LLM is
        configured.

        Body: ``{"room_id": "...", "content": "Prends le fait 2"}``
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                A2AResponse.error_response(INVALID_PARAMS, "invalid JSON body").to_dict(),
                status_code=400,
            )

        room_id = body.get("room_id", "")
        content = body.get("content", "")

        if not room_id or not content:
            return JSONResponse(
                A2AResponse.error_response(
                    INVALID_PARAMS, "room_id and content required"
                ).to_dict(),
                status_code=422,
            )

        # Find conflicts by room_id
        rows = self.store.conn.execute(
            "SELECT id, fact_a_id, fact_b_id FROM conflicts WHERE band_room_id=? AND status='open'",
            (room_id,),
        ).fetchall()

        if not rows:
            logger.info("Band webhook: no open conflict for room %s", room_id)
            return JSONResponse({"status": "ignored", "reason": "no open conflict for this room"})

        conflict_id, fact_a_id, fact_b_id = rows[0]

        # ── LLM-powered resolution ──
        winner_id, reason = await self._parse_resolution_intent(
            content, fact_a_id, fact_b_id
        )

        if winner_id is not None:
            self.store.resolve(conflict_id, winner_id, reason=reason)
            logger.info(
                "Band webhook [LLM] resolved conflict %s → fact %d: %s",
                conflict_id, winner_id, reason[:120],
            )
            return JSONResponse({
                "status": "resolved",
                "conflict_id": conflict_id,
                "fact_id": winner_id,
                "parser": "llm",
            })

        # ── Regex fallback ──
        match = re.search(r"resolve\s+with\s+fact\s+(\d+)", content, re.IGNORECASE)
        if not match:
            match = re.search(
                r"(?:prends?|choisis?|garde?|ok\s+pour|fact|fait)\s+(\d+)",
                content, re.IGNORECASE,
            )
        if match:
            fact_id = int(match.group(1))
            self.store.resolve(conflict_id, fact_id, reason=content)
            logger.info(
                "Band webhook [regex] resolved conflict %s → fact %d", conflict_id, fact_id
            )
            return JSONResponse({
                "status": "resolved",
                "conflict_id": conflict_id,
                "fact_id": fact_id,
                "parser": "regex",
            })

        return JSONResponse(
            {
                "status": "unresolved",
                "message": (
                    "Impossible de déterminer le fait à choisir. "
                    "Essayez: 'Prends le fait 1', 'OK pour le 2', "
                    "'Garde la version docs-repo'."
                ),
            },
        )

    async def _parse_resolution_intent(
        self, message: str, fact_a_id: int, fact_b_id: int
    ) -> tuple[int | None, str]:
        """Use LLM to extract the user's resolution intent from a natural language message.

        Returns ``(winner_id, reason)`` where ``winner_id`` is ``None`` when
        the LLM is unavailable or cannot determine a clear winner.
        """
        system = (
            "Tu es un agent d'analyse de message de chat. "
            "L'utilisateur repond a un conflit entre deux faits : "
            f"Fait A (ID={fact_a_id}) et Fait B (ID={fact_b_id}). "
            "Analyse sa reponse et retourne un JSON strict contenant :\n"
            '- "winner_id": entier (l\'ID du fait choisi, ou null si aucun choix clair)\n'
            '- "reason": string (explication concise de son choix)\n\n'
            "Exemples de reponses utilisateur et leur parsing :\n"
            f'- "Prends la version de la doc" -> {{"winner_id": <id doc>, "reason": "utilisateur prefere la source documentation"}}\n'
            '- "ok pour le fait 2" -> {"winner_id": 2, "reason": "choisit explicitement le fait 2"}\n'
            '- "ignore le premier" -> {"winner_id": <fact_b_id>, "reason": "veut ignorer le premier fait (A)"}\n'
            '- "garde FastAPI" -> {"winner_id": <id fastapi>, "reason": "choisit la valeur FastAPI"}\n'
            "Retourne uniquement du JSON brut - pas de markdown, pas de code fences."
        )

        raw = await provider.chat_completion(
            system, message,
            temperature=0.0,
            max_tokens=200,
            parse_json=True,
        )

        if not isinstance(raw, dict):
            return None, "LLM response was not a dict"

        winner_id = raw.get("winner_id")
        if winner_id is None:
            return None, "LLM could not determine winner"

        try:
            wid = int(winner_id)
        except (ValueError, TypeError):
            return None, f"LLM returned invalid winner_id: {winner_id!r}"

        if wid not in (fact_a_id, fact_b_id):
            return None, f"LLM returned winner_id {wid} not in ({fact_a_id}, {fact_b_id})"

        return wid, str(raw.get("reason", message))

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------

    async def health_checks(self) -> list[dict]:
        """Check connectivity to Keeper and Band."""
        checks: list[dict] = []

        # Keeper probe
        try:
            await a2a_call(
                self.keeper_url, "list-facts", {"limit": 1}, target_role="keeper", timeout=3.0
            )
            checks.append({"name": "keeper", "status": "UP", "detail": self.keeper_url})
        except Exception as e:
            checks.append({"name": "keeper", "status": "DOWN", "detail": str(e)})

        # Band probe
        if self.band:
            checks.append({"name": "band", "status": "UP", "detail": "configured"})
        else:
            checks.append({"name": "band", "status": "UP", "detail": "not configured (optional)"})

        return checks

    async def aclose(self) -> None:
        self.store.close()
        if self.band:
            await self.band.aclose()



# ------------------------------------------------------------------
# Web Dashboard HTML Template (Futuristic Neo-Brutalist / Dark Mode)
# ------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>A2A Knowledge Mesh — Dashboard</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
        
        :root {
            --bg-dark: #07080c;
            --card-bg: rgba(17, 19, 28, 0.75);
            --card-bg-hover: rgba(26, 29, 43, 0.85);
            --border-color: rgba(255, 255, 255, 0.05);
            --border-hover: rgba(255, 255, 255, 0.1);
            --text-primary: #ffffff;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            
            --accent-cyan: #00f2fe;
            --accent-green: #39ff14;
            --accent-purple: #a259ff;
            --accent-danger: #ff3b30;
            --accent-success: #10b981;
            
            --shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.5);
            --glow-cyan: 0 0 15px rgba(0, 242, 254, 0.2);
            --glow-purple: 0 0 15px rgba(162, 89, 255, 0.2);
            --glow-green: 0 0 15px rgba(57, 255, 20, 0.2);
            --glow-danger: 0 0 15px rgba(255, 59, 48, 0.2);
        }
        
        * {
            box-sizing: border-box;
        }
        
        body {
            background-color: var(--bg-dark);
            color: var(--text-primary);
            font-family: 'Outfit', sans-serif;
            margin: 0;
            padding: 0;
            min-height: 100vh;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(0, 242, 254, 0.04) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(162, 89, 255, 0.04) 0%, transparent 40%);
            background-attachment: fixed;
        }
        
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 40px;
            border-bottom: 1px solid var(--border-color);
            background: rgba(7, 8, 12, 0.8);
            backdrop-filter: blur(8px);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .logo-container {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo-icon {
            width: 32px;
            height: 32px;
            fill: none;
            stroke: var(--accent-purple);
            stroke-width: 2.5;
            filter: drop-shadow(0 0 8px var(--accent-purple));
        }
        
        h1 {
            font-size: 22px;
            font-weight: 600;
            margin: 0;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, #fff 0%, #a259ff 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .status-badge {
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.2);
            color: var(--accent-success);
            padding: 6px 14px;
            border-radius: 99px;
            font-size: 13px;
            font-weight: 500;
        }
        
        .pulse {
            width: 8px;
            height: 8px;
            background-color: var(--accent-success);
            border-radius: 50%;
            animation: pulse-animation 2s infinite;
        }
        
        @keyframes pulse-animation {
            0% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
            }
            70% {
                transform: scale(1);
                box-shadow: 0 0 0 6px rgba(16, 185, 129, 0);
            }
            100% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
            }
        }
        
        main {
            max-width: 1400px;
            margin: 30px auto;
            padding: 0 30px;
            display: grid;
            grid-template-columns: 320px 1fr;
            gap: 30px;
        }
        
        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 30px;
        }
        
        .content-panel {
            display: flex;
            flex-direction: column;
            gap: 30px;
        }
        
        .card {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            box-shadow: var(--shadow);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        .card:hover {
            border-color: var(--border-hover);
        }
        
        .card-title {
            font-size: 16px;
            font-weight: 600;
            margin-top: 0;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 8px;
            border-left: 3px solid var(--accent-purple);
            padding-left: 10px;
        }
        
        .registry-title { border-color: var(--accent-cyan); }
        .keeper-title { border-color: var(--accent-green); }
        
        /* Agent Node Graph */
        .node-detail {
            margin-top: 15px;
            font-size: 13px;
            background: rgba(0, 0, 0, 0.2);
            padding: 12px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            line-height: 1.4;
        }
        
        /* Agent Grid */
        .agent-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
        .agent-item {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 12px;
            font-size: 13px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .agent-info {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        
        .agent-name {
            font-weight: 600;
        }
        
        .agent-skills {
            font-size: 11px;
            color: var(--text-secondary);
        }
        
        /* Conflict Stream */
        .conflict-stream {
            display: flex;
            flex-direction: column;
            gap: 24px;
        }
        
        .conflict-card {
            border-radius: 16px;
            overflow: hidden;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            box-shadow: var(--shadow);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        .conflict-card:hover {
            border-color: rgba(255, 255, 255, 0.08);
        }
        
        .conflict-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 18px 24px;
            background: rgba(255, 255, 255, 0.02);
            border-bottom: 1px solid var(--border-color);
        }
        
        .conflict-meta {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .badge-conflict {
            background: rgba(255, 59, 48, 0.1);
            color: var(--accent-danger);
            border: 1px solid rgba(255, 59, 48, 0.2);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .badge-resolved {
            background: rgba(16, 185, 129, 0.1);
            color: var(--accent-success);
            border: 1px solid rgba(16, 185, 129, 0.2);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .conflict-subject {
            font-weight: 600;
            font-size: 15px;
        }
        
        .conflict-predicate {
            color: var(--text-secondary);
            font-size: 15px;
        }
        
        .conflict-body {
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        
        .comparison-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        
        .fact-panel {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 16px;
            position: relative;
        }
        
        .fact-panel.selected {
            border-color: var(--accent-success);
            background: rgba(16, 185, 129, 0.02);
        }
        
        .fact-panel.suggested {
            border-color: var(--accent-purple);
            background: rgba(162, 89, 255, 0.02);
        }
        
        .fact-source {
            font-size: 11px;
            color: var(--text-secondary);
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .fact-source-badge {
            background: rgba(255, 255, 255, 0.05);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 10px;
        }
        
        .fact-value {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            word-break: break-all;
        }
        
        .fact-url {
            font-size: 11px;
            color: var(--accent-cyan);
            text-decoration: none;
            margin-top: 10px;
            display: inline-block;
            word-break: break-all;
        }
        
        .fact-url:hover {
            text-decoration: underline;
        }
        
        /* AI Suggestion Card */
        .ai-suggestion {
            background: linear-gradient(135deg, rgba(162, 89, 255, 0.05) 0%, rgba(0, 242, 254, 0.02) 100%);
            border: 1px dashed var(--accent-purple);
            border-radius: 12px;
            padding: 16px;
            display: flex;
            align-items: flex-start;
            gap: 14px;
            box-shadow: var(--glow-purple);
        }
        
        .ai-icon-bg {
            background: var(--accent-purple);
            padding: 8px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
            filter: drop-shadow(0 0 6px var(--accent-purple));
        }
        
        .ai-content {
            font-size: 13px;
        }
        
        .ai-title {
            font-weight: 600;
            color: var(--accent-purple);
            margin-bottom: 4px;
            font-size: 14px;
        }
        
        .ai-reason {
            color: var(--text-secondary);
            line-height: 1.5;
        }
        
        /* Resolution actions */
        .resolution-actions {
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            border-top: 1px solid var(--border-color);
            padding-top: 18px;
        }
        
        .btn {
            font-family: inherit;
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 500;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
        
        .btn-outline {
            background: transparent;
            border: 1px solid var(--border-color);
            color: #fff;
        }
        
        .btn-outline:hover {
            background: rgba(255, 255, 255, 0.05);
            border-color: var(--border-hover);
        }
        
        .btn-primary {
            background: var(--accent-purple);
            border: 1px solid var(--accent-purple);
            color: #fff;
            box-shadow: var(--glow-purple);
        }
        
        .btn-primary:hover {
            background: #8b3ef7;
            box-shadow: 0 0 20px rgba(162, 89, 255, 0.4);
        }
        
        .resolution-summary {
            background: rgba(16, 185, 129, 0.05);
            border: 1px solid rgba(16, 185, 129, 0.2);
            border-radius: 8px;
            padding: 12px 18px;
            font-size: 13px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: var(--accent-success);
            width: 100%;
        }
        
        /* Facts Table */
        .facts-explorer {
            grid-column: span 2;
        }
        
        .table-container {
            overflow-x: auto;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            background: rgba(0, 0, 0, 0.2);
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }
        
        th {
            background: rgba(255, 255, 255, 0.02);
            padding: 14px 18px;
            font-weight: 600;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border-color);
        }
        
        td {
            padding: 14px 18px;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-primary);
        }
        
        tr:last-child td {
            border-bottom: none;
        }
        
        tr:hover td {
            background: rgba(255, 255, 255, 0.01);
        }
        
        .search-bar {
            width: 100%;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 10px 14px;
            color: #fff;
            font-family: inherit;
            font-size: 13px;
            margin-bottom: 16px;
            outline: none;
            transition: all 0.2s ease;
        }
        
        .search-bar:focus {
            border-color: var(--accent-purple);
            background: rgba(255, 255, 255, 0.05);
        }
        
        .empty-state {
            padding: 40px;
            text-align: center;
            color: var(--text-muted);
            font-size: 14px;
        }

        @keyframes dash {
            to {
                stroke-dashoffset: -40;
            }
        }
        .pulse-flow {
            animation: dash 2s linear infinite;
        }
        .node-g {
            cursor: pointer;
            transition: transform 0.2s ease;
        }
        .node-g:hover {
            transform: scale(1.08);
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-container">
            <svg class="logo-icon" viewBox="0 0 24 24">
                <polygon points="12 2 2 22 22 22" />
                <circle cx="12" cy="10" r="2" fill="currentColor" />
            </svg>
            <div>
                <h1>A2A Knowledge Mesh</h1>
                <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px;">Coordination Layer & Reconciler</div>
            </div>
        </div>
        <div class="status-badge">
            <div class="pulse"></div>
            Mesh actif (Band coord.)
        </div>
    </header>
    
    <main>
        <div class="sidebar">
            <div class="card">
                <h3 class="card-title">Topologie A2A</h3>
                <svg width="100%" height="220" viewBox="0 0 300 220" style="background: rgba(0,0,0,0.15); border-radius: 10px; border: 1px solid var(--border-color);">
                    <path id="flow-r-k" d="M 150 40 L 70 160" stroke="rgba(255,255,255,0.06)" stroke-width="2" fill="none" />
                    <path id="flow-k-rc" d="M 70 160 L 230 160" stroke="rgba(255,255,255,0.06)" stroke-width="2" fill="none" />
                    <path id="flow-rc-r" d="M 230 160 L 150 40" stroke="rgba(255,255,255,0.06)" stroke-width="2" fill="none" />
                    
                    <path id="pulse-r-k" d="M 150 40 L 70 160" stroke="var(--accent-cyan)" stroke-width="2" stroke-dasharray="6 6" fill="none" class="pulse-flow" />
                    <path id="pulse-k-rc" d="M 70 160 L 230 160" stroke="var(--accent-green)" stroke-width="2" stroke-dasharray="6 6" fill="none" class="pulse-flow" />
                    <path id="pulse-rc-r" d="M 230 160 L 150 40" stroke="var(--accent-purple)" stroke-width="2" stroke-dasharray="6 6" fill="none" class="pulse-flow" />
                    
                    <circle cx="150" cy="40" r="16" fill="transparent" stroke="var(--accent-cyan)" stroke-width="1" stroke-dasharray="2 2" />
                    <circle cx="70" cy="160" r="16" fill="transparent" stroke="var(--accent-green)" stroke-width="1" stroke-dasharray="2 2" />
                    <circle cx="230" cy="160" r="16" fill="transparent" stroke="var(--accent-purple)" stroke-width="1" stroke-dasharray="2 2" />
                    
                    <g class="node-g" onclick="selectNode('registry')">
                        <circle cx="150" cy="40" r="14" fill="#0c0e16" stroke="var(--accent-cyan)" stroke-width="2" />
                        <text x="150" y="43" text-anchor="middle" fill="#fff" font-size="8" font-weight="600">REG</text>
                    </g>
                    <g class="node-g" onclick="selectNode('keeper')">
                        <circle cx="70" cy="160" r="14" fill="#0c0e16" stroke="var(--accent-green)" stroke-width="2" />
                        <text x="70" y="163" text-anchor="middle" fill="#fff" font-size="8" font-weight="600">KEEP</text>
                    </g>
                    <g class="node-g" onclick="selectNode('reconciler')">
                        <circle cx="230" cy="160" r="14" fill="#0c0e16" stroke="var(--accent-purple)" stroke-width="2" />
                        <text x="230" y="163" text-anchor="middle" fill="#fff" font-size="8" font-weight="600">RECON</text>
                    </g>
                </svg>
                <div id="node-details" class="node-detail">
                    Cliquez sur un agent pour voir sa carte de compétences.
                </div>
            </div>
            
            <div class="card">
                <h3 class="card-title registry-title">Annuaire Registry</h3>
                <div class="agent-list" id="registry-agents-list">
                </div>
            </div>
        </div>
        
        <div class="content-panel">
            <div class="card">
                <h3 class="card-title">Flux des Contradictions</h3>
                <div class="conflict-stream" id="conflicts-stream-list">
                </div>
            </div>
        </div>
        
        <div class="card facts-explorer">
            <h3 class="card-title keeper-title">Explorateur des Faits Ingestés (Keeper)</h3>
            <input type="text" id="facts-search" class="search-bar" placeholder="Rechercher par sujet ou prédicat..." oninput="filterFacts()">
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 80px;">ID</th>
                            <th>Sujet</th>
                            <th>Prédicat</th>
                            <th>Valeur</th>
                            <th>Source</th>
                            <th>Timestamp</th>
                        </tr>
                    </thead>
                    <tbody id="facts-table-body">
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <script>
        let cachedData = {};
        const agentDetails = {
            registry: {
                name: "Registry Agent",
                port: 8765,
                desc: "Découverte et enregistrement d'agents autonomes. Publie /.well-known/agent-card.json."
            },
            keeper: {
                name: "Keeper Agent",
                port: 8766,
                desc: "Base de faits RDF-lite décentralisée. Détection des conflits via SQL JOIN indexé."
            },
            reconciler: {
                name: "Reconciler Agent",
                port: 8767,
                desc: "Orchestrateur de résolution. Interroge Featherless/OpenAI, notifie les salons Band et reçoit les webhooks."
            }
        };

        function selectNode(agentKey) {
            const details = agentDetails[agentKey];
            const div = document.getElementById('node-details');
            div.innerHTML = `
                <div style="font-weight: 600; color: #fff; margin-bottom: 4px;">\${details.name}</div>
                <div style="font-size: 11px; margin-bottom: 6px; color: var(--text-muted)">Port local: \${details.port}</div>
                <div style="line-height: 1.4">\${details.desc}</div>
            `;
        }

        async function fetchData() {
            try {
                const res = await fetch('/api/dashboard/data');
                const data = await res.json();
                cachedData = data;
                
                updateRegistryAgents(data.registry_agents);
                updateConflicts(data.conflicts, data.facts);
                updateFactsTable(data.facts);
                updateTopologyVisuals(data.conflicts);
            } catch (err) {
                console.error("Failed to fetch dashboard data:", err);
            }
        }

        function updateTopologyVisuals(conflicts) {
            const hasOpen = conflicts.some(c => c.status === 'open');
            const pulseLine = document.getElementById('pulse-k-rc');
            if (pulseLine) {
                if (hasOpen) {
                    pulseLine.setAttribute('stroke', 'var(--accent-danger)');
                    pulseLine.style.animationDuration = '0.8s';
                } else {
                    pulseLine.setAttribute('stroke', 'var(--accent-green)');
                    pulseLine.style.animationDuration = '2s';
                }
            }
        }

        function updateRegistryAgents(agents) {
            const list = document.getElementById('registry-agents-list');
            if (!agents || agents.length === 0) {
                list.innerHTML = \`<div class="empty-state">Aucun agent enregistré</div>\`;
                return;
            }
            
            list.innerHTML = agents.map(a => \`
                <div class="agent-item">
                    <div class="agent-info">
                        <div class="agent-name">\${a.agent_id}</div>
                        <div class="agent-skills">\${a.skills.join(', ')}</div>
                    </div>
                    <div style="font-size: 11px; color: var(--accent-cyan); background: rgba(0, 242, 254, 0.05); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(0, 242, 254, 0.1)">
                        \${a.port}
                    </div>
                </div>
            \`).join('');
        }

        function updateConflicts(conflicts, facts) {
            const list = document.getElementById('conflicts-stream-list');
            if (!conflicts || conflicts.length === 0) {
                list.innerHTML = \`<div class="empty-state">Aucune contradiction détectée en base</div>\`;
                return;
            }

            const factsMap = {};
            facts.forEach(f => {
                factsMap[f.id] = f;
            });

            list.innerHTML = conflicts.map(c => {
                const factA = factsMap[c.fact_a_id] || { object: 'Indisponible', source_id: c.source_a || 'Inconnu' };
                const factB = factsMap[c.fact_b_id] || { object: 'Indisponible', source_id: c.source_b || 'Inconnu' };
                const isResolved = c.status === 'resolved';

                let aiRecommendHtml = '';
                if (c.ai_suggested_fact_id) {
                    const aiWinner = c.ai_suggested_fact_id === c.fact_a_id ? factA : factB;
                    aiRecommendHtml = \`
                        <div class="ai-suggestion">
                            <div class="ai-icon-bg">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M12 2a10 10 0 0 1 10 10c0 5.523-4.477 10-10 10S2 17.523 2 12A10 10 0 0 1 12 2z"/>
                                    <path d="M12 6v6l4 2"/>
                                </svg>
                            </div>
                            <div class="ai-content">
                                <div class="ai-title">Suggestion de l'IA</div>
                                <div class="ai-reason">
                                    Fait suggéré: <strong>Fact #\${c.ai_suggested_fact_id} (\${aiWinner.object})</strong><br>
                                    <span style="font-size: 11px; margin-top: 4px; display: inline-block;">Raison: \${c.ai_reason || 'Aucune raison donnée.'}</span>
                                </div>
                            </div>
                        </div>
                    \`;
                }

                let actionHtml = '';
                if (!isResolved) {
                    actionHtml = \`
                        <div class="resolution-actions">
                            <button class="btn btn-outline" onclick="resolveConflict('\${c.conflict_id}', \${c.fact_a_id}, 'Choix manuel de la version fact A')">
                                Choisir Fact #\${c.fact_a_id}
                            </button>
                            <button class="btn btn-outline" onclick="resolveConflict('\${c.conflict_id}', \${c.fact_b_id}, 'Choix manuel de la version fact B')">
                                Choisir Fact #\${c.fact_b_id}
                            </button>
                            \${c.ai_suggested_fact_id ? \`
                                <button class="btn btn-primary" onclick="resolveConflict('\${c.conflict_id}', \${c.ai_suggested_fact_id}, 'Acceptation de la recommandation de l\\\\\\'IA')">
                                    Accepter la suggestion IA
                                </button>
                            \` : ''}
                        </div>
                    \`;
                } else {
                    actionHtml = \`
                        <div class="resolution-summary">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                                    <polyline points="20 6 9 17 4 12" />
                                </svg>
                                <span>Gagnant résolu : <strong>Fact #\${c.resolution_fact_id}</strong></span>
                            </div>
                            <div style="font-size: 11px; opacity: 0.8;">
                                Raison : \${c.resolution_reason || 'N/A'}
                            </div>
                        </div>
                    \`;
                }

                return \`
                    <div class="conflict-card">
                        <div class="conflict-header">
                            <div class="conflict-meta">
                                <span class="\${isResolved ? 'badge-resolved' : 'badge-conflict'}">\${c.status}</span>
                                <div>
                                    <span class="conflict-subject">\${c.subject}</span>
                                    <span class="conflict-predicate"> / \${c.predicate}</span>
                                </div>
                            </div>
                            <div style="font-size: 11px; color: var(--text-muted);">
                                ID: \${c.conflict_id}
                            </div>
                        </div>
                        <div class="conflict-body">
                            <div class="comparison-grid">
                                <div class="fact-panel \${isResolved && c.resolution_fact_id === c.fact_a_id ? 'selected' : ''} \${!isResolved && c.ai_suggested_fact_id === c.fact_a_id ? 'suggested' : ''}">
                                    <div class="fact-source">
                                        <span>Fact #\${c.fact_a_id}</span>
                                        <span class="fact-source-badge">\${factA.source_id}</span>
                                    </div>
                                    <div class="fact-value">\${factA.object}</div>
                                    \${factA.source_url ? \`<a href="\${factA.source_url}" target="_blank" class="fact-url">\${factA.source_url.split('/').pop()}</a>\` : ''}
                                </div>
                                <div class="fact-panel \${isResolved && c.resolution_fact_id === c.fact_b_id ? 'selected' : ''} \dots \${!isResolved && c.ai_suggested_fact_id === c.fact_b_id ? 'suggested' : ''}">
                                    <div class="fact-source">
                                        <span>Fact #\${c.fact_b_id}</span>
                                        <span class="fact-source-badge">\${factB.source_id}</span>
                                    </div>
                                    <div class="fact-value">\${factB.object}</div>
                                    \${factB.source_url ? \`<a href="\${factB.source_url}" target="_blank" class="fact-url">\${factB.source_url.split('/').pop()}</a>\` : ''}
                                </div>
                            </div>
                            
                            \${aiRecommendHtml}
                            
                            \${actionHtml}
                        </div>
                    </div>
                \`;
            }).join('');
        }

        function updateFactsTable(facts) {
            window.allFacts = facts;
            filterFacts();
        }

        function filterFacts() {
            const query = document.getElementById('facts-search').value.toLowerCase();
            const body = document.getElementById('facts-table-body');
            const facts = window.allFacts || [];
            
            const filtered = facts.filter(f => 
                f.subject.toLowerCase().includes(query) || 
                f.predicate.toLowerCase().includes(query) ||
                f.object.toLowerCase().includes(query)
            );
            
            if (filtered.length === 0) {
                body.innerHTML = \`<tr><td colspan="6" class="empty-state">Aucun fait correspondant en base</td></tr>\`;
                return;
            }

            body.innerHTML = filtered.map(f => \`
                <tr>
                    <td style="font-weight: 600; color: var(--accent-cyan);">#\${f.id}</td>
                    <td style="font-weight: 500;">\${f.subject}</td>
                    <td><span style="background: rgba(255,255,255,0.03); padding: 2px 6px; border-radius: 4px; font-size: 11px;">\${f.predicate}</span></td>
                    <td style="font-weight: 600; color: #fff;">\${f.object}</td>
                    <td><span class="fact-source-badge">\${f.source_id}</span></td>
                    <td style="color: var(--text-muted); font-size: 11px;">\${new Date(f.timestamp * 1000).toLocaleString()}</td>
                </tr>
            \`).join('');
        }

        async function resolveConflict(conflictId, winnerFactId, reason) {
            try {
                const res = await fetch('/api/dashboard/resolve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        conflict_id: conflictId,
                        winner_fact_id: winnerFactId,
                        reason: reason
                    })
                });
                const result = await res.json();
                if (result.status === 'success') {
                    fetchData();
                } else {
                    alert('Erreur: ' + result.error);
                }
            } catch (err) {
                alert('Erreur lors de la résolution du conflit.');
            }
        }

        fetchData();
        setInterval(fetchData, 3000);
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    band_id = os.getenv("BAND_AGENT_ID")
    band_key = os.getenv("BAND_API_KEY")
    ReconcilerAgent(band_agent_id=band_id, band_api_key=band_key).run()
