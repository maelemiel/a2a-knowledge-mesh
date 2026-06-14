"""A2A protocol helpers — JSON-RPC types and Agent Card schema."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentCard:
    name: str
    description: str
    url: str
    skills: list[str]
    version: str = "1.0.0"
    authentication: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "skills": self.skills,
            "version": self.version,
            "authentication": self.authentication,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AgentCard:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            url=d["url"],
            skills=d.get("skills", []),
            version=d.get("version", "1.0.0"),
            authentication=d.get("authentication"),
        )


@dataclass
class A2ARequest:
    """JSON-RPC 2.0 request for A2A protocol."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"
    id: str = "req-001"

    def to_dict(self) -> dict:
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, d: dict) -> A2ARequest:
        return cls(
            jsonrpc=d.get("jsonrpc", "2.0"),
            id=d.get("id", ""),
            method=d["method"],
            params=d.get("params", {}),
        )


@dataclass
class A2AResponse:
    result: Any = None
    error: str | None = None
    jsonrpc: str = "2.0"
    id: str = ""

    def to_dict(self) -> dict:
        if self.error:
            return {
                "jsonrpc": self.jsonrpc,
                "id": self.id,
                "error": {"code": -32000, "message": self.error},
            }
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "result": self.result,
        }


def parse_request(body: bytes | str) -> A2ARequest:
    if isinstance(body, bytes):
        body = body.decode()
    return A2ARequest.from_dict(json.loads(body))
