"""Input validation for A2A Knowledge Mesh — Pydantic schemas.

Every RPC parameter payload is validated against a model before
reaching store methods.  Rejects malformed / oversized / wrong-type
data at the boundary, not mid-call.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Keeper: fact operations
# ---------------------------------------------------------------------------


class StoreFactParams(BaseModel):
    """Shape of ``store-fact`` RPC params."""

    subject: str = Field(..., min_length=1, max_length=256, description="Entity name")
    predicate: str = Field(..., min_length=1, max_length=128, description="Attribute name")
    object: str = Field(..., min_length=1, max_length=4096, description="Value")
    source_id: str = Field(default="default", max_length=128)
    source_url: str | None = Field(default=None, max_length=2048)

    @field_validator("source_id")
    @classmethod
    def source_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            msg = "source_id must not be empty"
            raise ValueError(msg)
        return v.strip()


class StoreFactsBatchParams(BaseModel):
    """Shape of ``store-facts-batch`` RPC params (bulk insert)."""

    facts: list[StoreFactParams] = Field(..., min_length=1, max_length=500)


class RecallParams(BaseModel):
    """Shape of ``recall`` RPC params."""

    subject: str | None = Field(default=None, max_length=256)
    source_id: str | None = Field(default=None, max_length=128)


class ListFactsParams(BaseModel):
    """Shape of ``list-facts`` RPC params."""

    limit: int = Field(default=50, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Registry: agent operations
# ---------------------------------------------------------------------------


class RegisterParams(BaseModel):
    """Shape of ``register`` RPC params."""

    agent_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=128)
    card_url: str = Field(..., max_length=2048)
    skills: list[str] = Field(..., min_length=1, max_length=50)
    url: str = Field(..., max_length=2048)

    @field_validator("skills")
    @classmethod
    def skills_not_empty(cls, v: list[str]) -> list[str]:
        for s in v:
            if not s.strip():
                msg = "skill names must not be empty"
                raise ValueError(msg)
        return [s.strip() for s in v]


class DiscoverParams(BaseModel):
    """Shape of ``discover`` RPC params."""

    skill: str = Field(default="", max_length=128)


# ---------------------------------------------------------------------------
# Reconciler: conflict operations
# ---------------------------------------------------------------------------


class DetectConflictParams(BaseModel):
    """Shape of ``detect-conflict`` RPC params."""

    keeper_url: str | None = Field(default=None, max_length=2048)


class ResolveParams(BaseModel):
    """Shape of ``resolve`` RPC params."""

    conflict_id: str = Field(..., min_length=1, max_length=64)
    resolution_fact_id: int = Field(..., gt=0)
    reason: str = Field(default="", max_length=4096)
