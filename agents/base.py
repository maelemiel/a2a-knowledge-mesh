"""Base Agent — A2A server, auth, health, card, and RPC dispatch.

Every agent subclasses ``Agent`` and implements:
- ``card`` — AgentCard class attribute
- ``handle_rpc(method, params) -> Any``

The base class provides:
- Auth middleware (bearer token + optional HMAC)
- JSON-RPC 2.0 compliant request validation and error mapping
- Dependency-aware health endpoint
- Agent card publishing
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agents.auth import A2AAuthMiddleware
from protocols.a2a import (
    INVALID_PARAMS,
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    AgentCard,
    A2AResponse,
    parse_request,
)

if TYPE_CHECKING:
    from sqlite3 import Connection

logger = logging.getLogger(__name__)


class Agent(ABC):
    """Base class for all A2A agents.

    Subclasses must set:
    - ``card``: AgentCard class attribute
    - ``handle_rpc(method, params) -> Any``: RPC dispatcher

    Subclasses may override:
    - ``health_checks() -> list[dict]``: return dependency status dicts
    - ``connection``: SQLite connection for health probe
    """

    card: AgentCard
    port: int = 8765
    agent_role: str = "agent"  # used by auth middleware token lookup
    connection: Connection | None = None  # set by subclass for DB health

    def __init__(self) -> None:
        import contextlib

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            yield
            await self.shutdown_agent()

        self._starlette = Starlette(
            routes=[
                Route("/.well-known/agent-card.json", self.get_card),
                Route("/health", self.health),
                Route("/a2a", self.rpc, methods=["POST"]),
            ],
            lifespan=lifespan,
        )
        # Wrap with ASGI auth middleware.
        self.app = A2AAuthMiddleware(self._starlette, agent_role=self.agent_role)
        logger.info("Agent %r listening on port %d", self.card.name, self.port)

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def get_card(self, _request: Request) -> JSONResponse:
        return JSONResponse(self.card.to_dict())

    async def health(self, _request: Request) -> JSONResponse:
        checks = await self._run_health_checks()
        # Always return 200 — "UP" means this agent is alive.
        # Downstream dependency status is in the checks array.
        return JSONResponse(
            {"status": "UP", "agent": self.card.name, "checks": checks},
        )

    async def _run_health_checks(self) -> list[dict]:
        """Collect health signals from overridable ``health_checks()``."""
        results: list[dict] = []

        # Database probe
        if self.connection is not None:
            try:
                cur = self.connection.execute("SELECT 1")
                cur.fetchone()
                results.append({"name": "database", "status": "UP"})
            except Exception as exc:
                results.append({"name": "database", "status": "DOWN", "detail": str(exc)})
        else:
            results.append({"name": "database", "status": "UP", "detail": "not configured"})

        # Subclass-specific checks
        extra = await self.health_checks() if hasattr(self, "health_checks") else []
        results.extend(extra)

        return results

    async def health_checks(self) -> list[dict]:
        """Override in subclasses to add dependency checks.

        Each element: ``{"name": str, "status": "UP"|"DOWN", "detail?": str}``
        """
        return []

    # ------------------------------------------------------------------
    # JSON-RPC 2.0 dispatcher
    # ------------------------------------------------------------------

    async def rpc(self, request: Request) -> JSONResponse:
        t0 = time.perf_counter()
        body = await request.body()

        req, err = parse_request(body)
        if err is not None:
            logger.warning("Invalid RPC request: %s", err.error)
            return JSONResponse(err.to_dict(), status_code=400)

        assert req is not None  # parse_request guarantees this when err is None

        try:
            # Subclass dispatcher — call the sync or async handler
            result = await self._dispatch(req.method, req.params)
            elapsed = (time.perf_counter() - t0) * 1000
            logger.debug("RPC %s OK (%.1fms)", req.method, elapsed)
            return JSONResponse(A2AResponse(result=result, id=req.id).to_dict())
        except ValueError as e:
            # ValueError with "unknown method" → -32601
            msg = str(e)
            if msg.startswith("unknown method:"):
                return JSONResponse(
                    A2AResponse.error_response(METHOD_NOT_FOUND, msg, req_id=req.id).to_dict(),
                    status_code=404,
                )
            # ValueError from Pydantic → -32602
            return JSONResponse(
                A2AResponse.error_response(INVALID_PARAMS, msg, req_id=req.id).to_dict(),
                status_code=422,
            )
        except (TypeError, KeyError) as e:
            return JSONResponse(
                A2AResponse.error_response(INVALID_PARAMS, str(e), req_id=req.id).to_dict(),
                status_code=422,
            )
        except NotImplementedError:
            return JSONResponse(
                A2AResponse.error_response(
                    METHOD_NOT_FOUND, f"Method not implemented: {req.method}", req_id=req.id
                ).to_dict(),
                status_code=404,
            )
        except Exception as e:
            logger.exception("RPC %s failed", req.method)
            return JSONResponse(
                A2AResponse.error_response(
                    INTERNAL_ERROR, f"Internal error: {e}", req_id=req.id
                ).to_dict(),
                status_code=500,
            )

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Call ``handle_rpc``, supporting both sync and async implementations."""
        result = self.handle_rpc(method, params)
        if hasattr(result, "__await__"):
            return await result
        return result

    @abstractmethod
    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        """Dispatch the RPC method with validated params.

        Subclasses should:
        - Raise ``ValueError`` with "unknown method: {name}" for unknown methods.
        - Return a dict (or other JSON-serialisable value) on success.
        """

    # ------------------------------------------------------------------
    # Runner
    # ------------------------------------------------------------------

    async def shutdown_agent(self) -> None:
        """Teardown method to release resources on shutdown."""
        from agents.auth import close_a2a_client
        logger.info("Stopping agent %r, closing shared connection pool", self.card.name)
        await close_a2a_client()

    def run(self) -> None:
        uvicorn.run(self.app, host="0.0.0.0", port=self.port, log_level="info")
