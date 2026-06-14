"""Base Agent — A2A server, health, card, and RPC dispatch."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from protocols.a2a import AgentCard, A2AResponse, parse_request


class Agent(ABC):
    """Base class for all A2A agents.

    Subclasses must implement:
    - card: AgentCard class attribute
    - handle_rpc(method, params) -> Any
    """

    card: AgentCard
    port: int = 8765

    def __init__(self) -> None:
        self.app = Starlette(
            routes=[
                Route("/.well-known/agent-card.json", self.get_card),
                Route("/health", self.health),
                Route("/a2a", self.rpc, methods=["POST"]),
            ]
        )

    async def get_card(self, _request: Request) -> JSONResponse:
        return JSONResponse(self.card.to_dict())

    async def health(self, _request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "agent": self.card.name})

    async def rpc(self, request: Request) -> JSONResponse:
        body = await request.body()
        try:
            rpc_req = parse_request(body)
        except (json.JSONDecodeError, KeyError) as e:
            return JSONResponse(
                A2AResponse(error=f"invalid request: {e}").to_dict(), status_code=400
            )

        try:
            result = self.handle_rpc(rpc_req.method, rpc_req.params)
        except Exception as e:
            return JSONResponse(
                A2AResponse(error=str(e), id=rpc_req.id).to_dict(), status_code=500
            )

        return JSONResponse(
            A2AResponse(result=result, id=rpc_req.id).to_dict()
        )

    @abstractmethod
    def handle_rpc(self, method: str, params: dict[str, Any]) -> Any:
        ...

    def run(self) -> None:
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)
