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
                ai_reason TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conflicts_subject ON conflicts(subject)"
        )
        self.conn.commit()

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
    ) -> dict:
        conflict_id = str(uuid.uuid4())[:8]
        ts = int(time.time())
        self.conn.execute(
            "INSERT INTO conflicts (id, subject, predicate, fact_a_id, fact_b_id, "
            "source_a, source_b, created_at, ai_suggested_fact_id, ai_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conflict_id, subject, predicate, fact_a_id, fact_b_id,
             source_a, source_b, ts, ai_suggested_fact_id, ai_reason),
        )
        self.conn.commit()
        return {
            "conflict_id": conflict_id,
            "subject": subject,
            "predicate": predicate,
            "ai_suggested_fact_id": ai_suggested_fact_id,
            "ai_reason": ai_reason,
        }

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
            "band_room_id, created_at, ai_suggested_fact_id, ai_reason "
            "FROM conflicts WHERE status='open' ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "conflict_id": r[0], "subject": r[1], "predicate": r[2],
                "fact_a_id": r[3], "fact_b_id": r[4],
                "source_a": r[5], "source_b": r[6],
                "band_room_id": r[7], "created_at": r[8],
                "ai_suggested_fact_id": r[9], "ai_reason": r[10],
            }
            for r in rows
        ]

    def get_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, predicate, status, created_at, resolved_at, "
            "ai_suggested_fact_id, ai_reason "
            "FROM conflicts ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "conflict_id": r[0], "subject": r[1], "predicate": r[2],
                "status": r[3], "created_at": r[4], "resolved_at": r[5],
                "ai_suggested_fact_id": r[6], "ai_reason": r[7],
            }
            for r in rows
        ]

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

BAND_API_BASE = "https://api.band.ai/v2"


class BandError(Exception):
    """Raised when a Band API call fails after all retries."""


class BandClient:
    """Resilient Band REST API client with retry + structured logging.

    Uses ``httpx.AsyncClient`` throughout.
    """

    def __init__(self, agent_id: str, api_key: str, *, max_retries: int = 3) -> None:
        if not agent_id or not api_key:
            raise ValueError("BAND_AGENT_ID and BAND_API_KEY must be set")
        self.agent_id = agent_id
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=BAND_API_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def create_room(self, title: str) -> dict:
        """Create a room. Raises ``BandError`` on failure."""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.post(
                    f"/agents/{self.agent_id}/rooms",
                    json={"title": title},
                )
                if resp.is_success:
                    data = resp.json()
                    logger.info("Band room created: %s (title=%r)", data.get("id"), title)
                    return data

                logger.warning(
                    "Band create_room attempt %d/%d: HTTP %d %s",
                    attempt, self.max_retries, resp.status_code, resp.text[:200],
                )
                if resp.status_code == 429:
                    await _exponential_backoff(attempt)
                    continue
                if resp.status_code >= 500:
                    await _exponential_backoff(attempt)
                    continue

                msg = f"Band create_room failed: HTTP {resp.status_code} – {resp.text[:200]}"
                raise BandError(msg)

            except httpx.TimeoutException:
                logger.warning("Band create_room timeout attempt %d/%d", attempt, self.max_retries)
                if attempt < self.max_retries:
                    await _exponential_backoff(attempt)
                    continue
                raise BandError(f"Band create_room timeout after {self.max_retries} retries") from None

        raise BandError("Band create_room exhausted retries")

    async def post_message(self, room_id: str, message: str) -> dict:
        """Post a message to a room. Raises ``BandError`` on failure."""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.post(
                    f"/agents/{self.agent_id}/rooms/{room_id}/messages",
                    json={"content": message},
                )
                if resp.is_success:
                    return resp.json()

                logger.warning(
                    "Band post_message attempt %d/%d: HTTP %d",
                    attempt, self.max_retries, resp.status_code,
                )
                if resp.status_code in (429, 500, 502, 503):
                    await _exponential_backoff(attempt)
                    continue

                raise BandError(f"Band post_message HTTP {resp.status_code}: {resp.text[:200]}")

            except httpx.TimeoutException:
                logger.warning("Band post_message timeout attempt %d/%d", attempt, self.max_retries)
                if attempt < self.max_retries:
                    await _exponential_backoff(attempt)
                    continue
                raise BandError(f"Band post_message timeout after {self.max_retries} retries") from None

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


async def _get_ai_suggestion(a: dict, b: dict) -> tuple[int | None, str]:
    """Ask LLM which fact is correct; fallback to timestamp heuristic.

    Provider chain: Featherless → OpenAI → rule-based.
    Uses ``httpx.AsyncClient``.  Resilient JSON parsing.
    """
    featherless_key = os.getenv("FEATHERLESS_API_KEY") or os.getenv("FEATHERLESS_KEY")
    featherless_model = os.getenv("FEATHERLESS_MODEL", "Qwen/Qwen2.5-14B-Instruct")
    openai_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    api_key = None
    base_url = None
    model = None

    if featherless_key:
        api_key = featherless_key
        base_url = "https://api.featherless.ai/v1/chat/completions"
        model = featherless_model
    elif openai_key:
        api_key = openai_key
        base_url = "https://api.openai.com/v1/chat/completions"
        model = openai_model

    if not api_key:
        # Rule-based fallback when no LLM is configured
        winner_id = a["id"] if a["timestamp"] >= b["timestamp"] else b["id"]
        return winner_id, "[Fallback Rule] Most recent fact by timestamp (no LLM configured)."

    import asyncio
    from datetime import datetime as dt

    system_prompt = (
        "You are an expert AI data reconciliation agent. Compare two conflicting facts "
        'and respond with a JSON object containing exactly two fields:\n'
        '- "winner_id": integer (ID of the correct fact)\n'
        '- "reason": string (concise explanation)\n\n'
        "Return ONLY the raw JSON — no markdown, no code fences, no extra text."
    )

    user_content = (
        f"Compare conflicting facts:\n\n"
        f"Fact A (ID={a['id']}): {a['subject']} → {a['predicate']} = {a['object']}\n"
        f"  Source: {a['source_id']} | Timestamp: {dt.fromtimestamp(a['timestamp']).isoformat()}\n\n"
        f"Fact B (ID={b['id']}): {b['subject']} → {b['predicate']} = {b['object']}\n"
        f"  Source: {b['source_id']} | Timestamp: {dt.fromtimestamp(b['timestamp']).isoformat()}"
    )

    for attempt in range(1, 3):  # max 2 attempts per provider
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                assert base_url is not None  # already checked at top of function
                resp = await client.post(
                    base_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 300,
                    },
                )
                resp.raise_for_status()
                response_data = resp.json()

                # Defensive: handle non-standard response shapes
                choices = response_data.get("choices", [])
                if not choices:
                    msg = f"LLM returned 0 choices: {response_data.get('error', 'unknown')}"
                    raise ValueError(msg)

                raw_content = choices[0].get("message", {}).get("content", "")
                parsed = _parse_llm_json(raw_content)

                if parsed is None:
                    raise ValueError(f"LLM returned unparseable JSON: {raw_content[:200]}")

                winner_id = int(parsed["winner_id"])
                reason = str(parsed.get("reason", "No reason provided."))
                return winner_id, reason

        except Exception as exc:
            logger.warning("LLM attempt %d/2 failed: %s", attempt, exc)
            if attempt < 2:
                await asyncio.sleep(1.0)
            else:
                # All retries exhausted → fallback
                winner_id = a["id"] if a["timestamp"] >= b["timestamp"] else b["id"]
                reason = f"[Fallback Rule] LLM unavailable ({exc}). Recommended most recent by timestamp."
                return winner_id, reason

    # Should not reach here, but satisfy type checker
    return a["id"], "[Fallback Rule] Unexpected path."


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
        self._starlette.routes.append(
            Route("/band-webhook", self.band_webhook, methods=["POST"])
        )

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
            fact_a = {"id": c["fact_a_id"], "source_id": c["source_a"],
                      "object": c["object_a"], "timestamp": c["timestamp_a"],
                      "subject": c["subject"], "predicate": c["predicate"]}
            fact_b = {"id": c["fact_b_id"], "source_id": c["source_b"],
                      "object": c["object_b"], "timestamp": c["timestamp_b"],
                      "subject": c["subject"], "predicate": c["predicate"]}

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
                    logger.error("Band notification failed for conflict %s: %s", conflict["conflict_id"], e)

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
                A2AResponse.error_response(INVALID_PARAMS, "room_id and content required").to_dict(),
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
                {"status": "unresolved", "message": "no 'resolve with fact <ID>' pattern found in message"},
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
            await a2a_call(self.keeper_url, "list-facts", {"limit": 1}, target_role="keeper", timeout=3.0)
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
