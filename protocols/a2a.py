"""A2A protocol helpers — JSON-RPC 2.0 types and Agent Card schema.

Strictly complies with JSON-RPC 2.0 Specification (https://www.jsonrpc.org/specification).

Error codes used:
  -32700  Parse error        (invalid JSON)
  -32600  Invalid Request    (missing required fields)
  -32601  Method not found   (unknown RPC method)
  -32602  Invalid params     (wrong param types/values)
  -32603  Internal error     (unhandled server error)
  -32000  Custom server err  (reserved for mesh-specific errors)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 Error Codes (spec-defined)
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700      # Invalid JSON was received by the server.
INVALID_REQUEST = -32600  # The JSON sent is not a valid Request object.
METHOD_NOT_FOUND = -32601 # The method does not exist / is not available.
INVALID_PARAMS = -32602   # Invalid method parameter(s).
INTERNAL_ERROR = -32603   # Internal JSON-RPC error.
CUSTOM_ERROR = -32000     # Server-defined error (reserved for mesh-specific).


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------


@dataclass
class AgentCard:
    """A2A Agent Card — discoverable agent metadata.

    Extended with optional cryptographic identity so peers can verify
    the card publisher.  ``public_key`` is a PEM-encoded Ed25519 or
    ECDSA public key; ``signature`` is a base64-encoded signature of
    ``to_dict()`` (without the signature field itself) signed by the
    agent's private key.
    """

    name: str
    description: str
    url: str
    skills: list[str]
    version: str = "1.0.0"
    authentication: dict | None = None  # null → no auth required
    public_key: str | None = None  # PEM-encoded Ed25519 / ECDSA public key
    signature: str | None = None  # base64 sig over sorted canonical JSON of all other fields

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "skills": self.skills,
            "version": self.version,
            "authentication": self.authentication,
        }
        if self.public_key:
            d["publicKey"] = self.public_key
        if self.signature:
            d["signature"] = self.signature
        return d

    @classmethod
    def from_dict(cls, d: dict) -> AgentCard:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            url=d["url"],
            skills=d.get("skills", []),
            version=d.get("version", "1.0.0"),
            authentication=d.get("authentication"),
            public_key=d.get("publicKey"),
            signature=d.get("signature"),
        )


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 Request / Response
# ---------------------------------------------------------------------------


@dataclass
class A2ARequest:
    """JSON-RPC 2.0 request.

    ``validate()`` must be called before dispatching.
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"
    id: str | int | float | None = "req-001"

    @classmethod
    def from_dict(cls, d: dict) -> A2ARequest:
        return cls(
            jsonrpc=d.get("jsonrpc", ""),
            id=d.get("id"),
            method=d.get("method", ""),
            params=d.get("params", {}),
        )

    def validate(self) -> str | None:
        """Return error message if request is invalid, else None.

        Checks per JSON-RPC 2.0:
        - ``jsonrpc`` must be exactly ``"2.0"``
        - ``method`` must be a non-empty string
        - ``id`` may be string, number, or null (per spec)
        """
        if not isinstance(self.method, str) or not self.method.strip():
            return "missing or invalid 'method' (must be non-empty string)"
        if self.jsonrpc != "2.0":
            return "invalid 'jsonrpc' version (must be '2.0')"
        if self.id is not None and not isinstance(self.id, (str, int, float)):
            return "invalid 'id' (must be string, number, or null)"
        return None


@dataclass
class A2AResponse:
    """JSON-RPC 2.0 response.

    ``result`` and ``error`` are mutually exclusive per spec.
    """

    result: Any = None
    error: dict | None = None  # {"code": int, "message": str}
    jsonrpc: str = "2.0"
    id: str | int | float | None = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = self.error
        else:
            d["result"] = self.result
        return d

    @classmethod
    def error_response(cls, code: int, message: str, *, req_id: str | int | float | None = "") -> A2AResponse:
        """Build an error response with spec-compliant error object."""
        return cls(error={"code": code, "message": message}, id=req_id)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_request(body: bytes | str) -> tuple[A2ARequest | None, A2AResponse | None]:
    """Deserialise and validate an A2A request.

    Returns ``(request, None)`` on success or ``(None, error_response)``
    on parse/invalid-request failure.
    """
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as e:
        return None, A2AResponse.error_response(PARSE_ERROR, f"Parse error: {e}")

    if not isinstance(raw, dict):
        return None, A2AResponse.error_response(INVALID_REQUEST, "Request must be a JSON object")

    req = A2ARequest.from_dict(raw)
    err_msg = req.validate()
    if err_msg:
        return None, A2AResponse.error_response(INVALID_REQUEST, err_msg, req_id=req.id)

    return req, None
