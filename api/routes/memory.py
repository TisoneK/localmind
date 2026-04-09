"""
Memory API routes — F4

GET    /api/memory              — list all stored facts with metadata
DELETE /api/memory/{fact_id}   — delete a specific fact by ID
POST   /api/memory              — manually store a new fact
"""
from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storage.vector import VectorStore
from core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

_store = VectorStore()


# ── GET /api/memory ───────────────────────────────────────────────────────

@router.get("/memory")
async def list_memory():
    """Return all stored facts with metadata."""
    facts = await _store.list_all_with_metadata()
    return {"facts": facts, "count": len(facts)}


# ── DELETE /api/memory/{fact_id} ──────────────────────────────────────────

class DeleteResponse(BaseModel):
    deleted: bool
    fact_id: str


@router.delete("/memory/{fact_id}", response_model=DeleteResponse)
async def delete_memory(fact_id: str):
    """Delete a specific fact by its ID."""
    ok = await _store.forget_by_id(fact_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Fact '{fact_id}' not found or could not be deleted.")
    return DeleteResponse(deleted=True, fact_id=fact_id)


# ── POST /api/memory ──────────────────────────────────────────────────────

class StoreMemoryRequest(BaseModel):
    fact: str
    session_id: str = "manual"
    memory_type: str = "semantic"
    importance: float = 0.5
    source: str = "manual"


class StoreMemoryResponse(BaseModel):
    stored: bool
    fact: str


@router.post("/memory", response_model=StoreMemoryResponse)
async def store_memory(req: StoreMemoryRequest):
    """Manually store a fact in the vector memory store."""
    ok = await _store.store(
        fact=req.fact,
        session_id=req.session_id,
        source=req.source,
        extra_metadata={
            "memory_type": req.memory_type,
            "importance": str(req.importance),
            "access_count": "0",
        },
    )
    if not ok:
        raise HTTPException(status_code=503, detail="Memory store unavailable.")
    return StoreMemoryResponse(stored=True, fact=req.fact)
