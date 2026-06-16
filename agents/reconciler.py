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

import json
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
from agents.validation import DetectConflictParams, ResolveParams
from protocols.a2a import AgentCard, A2AResponse, INVALID_PARAMS

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

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


def _parse_llm_json(content: str) -> dict | None:
    """Resilient JSON parser for LLM output.

    Handles:
    - ```json ... ``` wrapping (with/without language tag)
    - Trailing commas
    - Leading/trailing whitespace and control chars
    - Incomplete truncated JSON (returns None)
    """
    if not content:
        return None

    cleaned = content.strip()
    # Remove markdown code fences
    if cleaned.startswith("```"):
        # Remove opening fence (with optional language tag)
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        # Remove closing fence if present
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    cleaned = cleaned.strip()
    if not cleaned:
        return None

    # Attempt strict parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt fix: strip trailing comma before closing braces
    try:
        import re as _re

        fixed = _re.sub(r",\s*}", "}", cleaned)
        fixed = _re.sub(r",\s*\]", "]", fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

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

    import asyncio

    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
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
        return winner["id"], "[Fallback Rule] Most recent fact by timestamp (no LLM configured)."

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

    import asyncio

    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                assert base_url is not None
                resp = await client.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
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
                await asyncio.sleep(1.5)

    # All retries exhausted — timestamp fallback
    winner = a if a["timestamp"] >= b["timestamp"] else b
    return winner["id"], "[Fallback Rule] LLM unavailable. Recommended most recent by timestamp."


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

    # ------------------------------------------------------------------
    # RPC dispatcher
    # ------------------------------------------------------------------

    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        if method == "detect-conflict":
            return self._detect(params)

        if method == "resolve":
            return self._resolve(params)

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

        ai_text = ""
        if conflict.get("ai_suggested_fact_id"):
            winner_fact = a if conflict["ai_suggested_fact_id"] == a["id"] else b
            ai_text = (
                f"\n**AI Recommendation:**\n"
                f"- Suggested winner: {winner_fact['object']}\n"
                f"- Reason: {conflict.get('ai_reason', 'N/A')}\n"
            )

        message = (
            f"**Conflict detected**\n"
            f"- **Subject:** {a['subject']}\n"
            f"- **Predicate:** {a['predicate']}\n\n"
            f"**Fact A** ({a['source_id']}): {a['object']} (ID: {a['id']})\n"
            f"**Fact B** ({b['source_id']}): {b['object']} (ID: {b['id']})\n"
            f"{ai_text}\n"
            f"To resolve, reply: `resolve with fact <ID> because <reason>`\n"
            f"Or use CLI: `mesh resolve {conflict['conflict_id']} <fact_id>`"
        )

        try:
            await self.band.post_message(room_id, message)
        except BandError as e:
            logger.error("Cannot post to Band room %s: %s", room_id, e)

    # ------------------------------------------------------------------
    # Band Webhook — push-based resolution
    # ------------------------------------------------------------------

    async def band_webhook(self, request: Request) -> JSONResponse:
        """Receive a webhook from Band when a user replies in a room.

        Expects::

            {"room_id": "...", "content": "resolve with fact 2 because pyproject.toml is authorititative"}

        Parses natural-language resolution patterns and applies them.
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
        conflicts = self.store.conn.execute(
            "SELECT id, fact_a_id, fact_b_id FROM conflicts WHERE band_room_id=? AND status='open'",
            (room_id,),
        ).fetchall()

        if not conflicts:
            logger.info("Band webhook: no open conflict for room %s", room_id)
            return JSONResponse({"status": "ignored", "reason": "no open conflict for this room"})

        # Parse "resolve with fact <N>" pattern
        match = re.search(r"resolve\s+with\s+fact\s+(\d+)", content, re.IGNORECASE)
        if not match:
            return JSONResponse(
                {
                    "status": "unresolved",
                    "message": "no 'resolve with fact <ID>' pattern found in message",
                },
            )

        fact_id = int(match.group(1))
        conflict_id = conflicts[0][0]

        self.store.resolve(conflict_id, fact_id, reason=content)
        logger.info("Band webhook resolved conflict %s → fact %d", conflict_id, fact_id)
        return JSONResponse({"status": "resolved", "conflict_id": conflict_id, "fact_id": fact_id})

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    band_id = os.getenv("BAND_AGENT_ID")
    band_key = os.getenv("BAND_API_KEY")
    ReconcilerAgent(band_agent_id=band_id, band_api_key=band_key).run()
