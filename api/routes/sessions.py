"""Session management endpoints."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storage.db import SessionStore
from core.config import settings

router = APIRouter()
_store = SessionStore(settings.localmind_db_path)


class SessionListItem(BaseModel):
    id: str
    created_at: float
    message_count: int
    last_active: float | None


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions():
    """List all conversation sessions."""
    return _store.list_sessions()


@router.get("/sessions/{session_id}/history")
async def get_history(session_id: str):
    """Get full message history for a session."""
    messages = _store.get_history(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Session not found or empty")
    return [
        {
            "role": m.role.value,
            "content": m.content,
            "timestamp": m.timestamp,
            "tool_name": m.tool_name,
        }
        for m in messages
    ]


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its messages."""
    deleted = _store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": session_id}
