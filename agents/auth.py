"""Authentication & authorization for A2A Knowledge Mesh.

Uses pre-shared bearer tokens per agent role (internal) + optional
master API key (external CLI).  HMAC-signs request bodies for
tamper-proof agent-to-agent calls.

Env vars required:
  A2A_REGISTRY_TOKEN      — bearer token for Registry Agent
  A2A_KEEPER_TOKEN        — bearer token for Keeper Agent
  A2A_RECONCILER_TOKEN    — bearer token for Reconciler Agent
  A2A_MASTER_TOKEN        — master token (CLI / external tools)
  A2A_HMAC_SECRET         — 32+ byte hex string for HMAC signing
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import httpx

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token registry — load once at import
# ---------------------------------------------------------------------------

_MASTER_TOKEN: str | None = os.getenv("A2A_MASTER_TOKEN", None)
_ROLE_TOKENS: dict[str, str] = {
    "registry": os.getenv("A2A_REGISTRY_TOKEN", ""),
    "keeper": os.getenv("A2A_KEEPER_TOKEN", ""),
    "reconciler": os.getenv("A2A_RECONCILER_TOKEN", ""),
}
_HMAC_SECRET: bytes = os.getenv("A2A_HMAC_SECRET", "").encode("ascii")


def configure_auth(
    *,
    master_token: str | None = None,
    registry_token: str = "",
    keeper_token: str = "",
    reconciler_token: str = "",
    hmac_secret: str = "",
) -> None:
    """Override defaults at runtime (useful for tests)."""
    global _MASTER_TOKEN, _ROLE_TOKENS, _HMAC_SECRET  # noqa: PLW0603
    if master_token is not None:
        _MASTER_TOKEN = master_token
    if registry_token:
        _ROLE_TOKENS["registry"] = registry_token
    if keeper_token:
        _ROLE_TOKENS["keeper"] = keeper_token
    if reconciler_token:
        _ROLE_TOKENS["reconciler"] = reconciler_token
    if hmac_secret:
        _HMAC_SECRET = hmac_secret.encode("ascii")


def _token_for_role(role: str) -> str:
    return _ROLE_TOKENS.get(role, "")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/.well-known/agent-card.json",
    }
)

ERROR_MISSING_TOKEN = -32001
ERROR_FORBIDDEN = -32003

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class A2AAuthMiddleware:
    """ASGI middleware that verifies bearer token on every non-public request.

    Replaces ``BaseHTTPMiddleware`` which has known issues with Starlette routing.
    Applied via ``app.add_middleware`` — Starlette wraps it as raw ASGI.
    """

    def __init__(self, app, *, agent_role: str):
        self.app = app
        self._role = agent_role
        self._expected_token = _token_for_role(agent_role)
        if not self._expected_token:
            logger.warning(
                "Auth middleware enabled but no token set for role %r. "
                "All non-public requests will be denied.",
                agent_role,
            )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        logger.debug("Auth middleware: path=%s method=%s", path, scope.get("method", ""))

        if path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract headers
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="replace")

        if not auth_header.startswith("Bearer "):
            logger.warning("Auth rejected: no bearer token for %s", path)
            response = JSONResponse(
                _error_body(ERROR_MISSING_TOKEN, "missing or malformed Authorization header"),
                status_code=401,
            )
            await response(scope, receive, send)
            return

        token = auth_header[7:]

        if token != self._expected_token and token != _MASTER_TOKEN:
            logger.warning("Auth rejected: invalid token for %s", path)
            response = JSONResponse(
                _error_body(ERROR_FORBIDDEN, "invalid token"),
                status_code=403,
            )
            await response(scope, receive, send)
            return

        # HMAC signature check
        if _HMAC_SECRET:
            hdr_sig = headers.get(b"x-a2a-signature", b"").decode("utf-8", errors="replace")
            if not hdr_sig:
                logger.warning("Auth rejected: missing HMAC signature for %s", path)
                response = JSONResponse(
                    _error_body(ERROR_FORBIDDEN, "missing x-a2a-signature header"),
                    status_code=403,
                )
                await response(scope, receive, send)
                return

            # Buffer body to verify HMAC signature
            body_chunks = []
            messages = []
            more_body = True
            while more_body:
                message = await receive()
                messages.append(message)
                more_body = message.get("more_body", False)
                body_chunks.append(message.get("body", b""))

            full_body = b"".join(body_chunks)
            if not _verify_hmac(full_body, hdr_sig):
                logger.warning("HMAC verification failed for %s", path)
                response = JSONResponse(
                    _error_body(ERROR_FORBIDDEN, "invalid HMAC signature"),
                    status_code=403,
                )
                await response(scope, receive, send)
                return

            # Replay buffered chunks to downstream ASGI app
            async def buffered_receive():
                if messages:
                    return messages.pop(0)
                return await receive()

            await self.app(scope, buffered_receive, send)
            return

        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


def sign_body(body: bytes) -> str:
    """Return ``hex(hmac_sha256(secret, body))``."""
    if not _HMAC_SECRET:
        logger.warning("HMAC_SECRET is empty; signing returns empty string.")
        return ""
    return hmac.new(_HMAC_SECRET, body, hashlib.sha256).hexdigest()


def _verify_hmac(body: bytes, signature: str) -> bool:
    if not _HMAC_SECRET or not signature:
        return False
    expected = hmac.new(_HMAC_SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# RPC caller helper (used by agents to call each other securely)
# ---------------------------------------------------------------------------

_A2A_CLIENT: httpx.AsyncClient | None = None


async def get_a2a_client() -> httpx.AsyncClient:
    """Get the shared/pooled httpx.AsyncClient instance."""
    global _A2A_CLIENT
    if _A2A_CLIENT is None or _A2A_CLIENT.is_closed:
        _A2A_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _A2A_CLIENT


async def close_a2a_client() -> None:
    """Close the shared httpx.AsyncClient instance if open."""
    global _A2A_CLIENT
    if _A2A_CLIENT is not None and not _A2A_CLIENT.is_closed:
        await _A2A_CLIENT.aclose()


async def a2a_call(
    url: str,
    method: str,
    params: dict | None = None,
    *,
    target_role: str = "",
    timeout: float = 10.0,
) -> dict:
    """Make an authenticated A2A JSON-RPC 2.0 call.

    Automatically attaches the bearer token for *target_role* and
    HMAC-signs the request body.
    """
    token = _token_for_role(target_role) if target_role else _MASTER_TOKEN
    if not token:  # fall back to master token
        token = _MASTER_TOKEN
    body_bytes = json.dumps(
        {"jsonrpc": "2.0", "id": f"a2a-{method}", "method": method, "params": params or {}}
    ).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    sig = sign_body(body_bytes)
    if sig:
        headers["X-A2A-Signature"] = sig

    client = await get_a2a_client()
    resp = await client.post(
        f"{url.rstrip('/')}/a2a",
        content=body_bytes,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"A2A error: {data['error']}")
    return data.get("result", {})


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _error_body(code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}
