"""
Chat endpoint — the primary API surface.

POST /api/chat  — multipart form with message + optional file
Streams response as Server-Sent Events (SSE).

SSE format:
    data: {"text": "token", "done": false}\n\n
    data: {"text": "", "done": true}\n\n
    data: {"error": "...", "done": true}\n\n
"""
from __future__ import annotations
import json
import uuid
import logging

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional

from core.engine import Engine
from core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# One engine instance per worker (stateless except for DB connection)
_engine = Engine()

MAX_FILE_BYTES = settings.localmind_max_file_size_mb * 1024 * 1024


async def _event_stream(message: str, session_id: str, file_bytes=None, filename=None, content_type=None):
    """Async generator that yields SSE-formatted chunks."""
    try:
        async for chunk in _engine.process(
            message=message,
            session_id=session_id,
            file=file_bytes,
            filename=filename,
            content_type=content_type,
        ):
            if chunk.error:
                payload = json.dumps({"error": chunk.error, "done": True})
                yield f"data: {payload}\n\n"
                return
            payload = json.dumps({"text": chunk.text, "done": chunk.done})
            yield f"data: {payload}\n\n"
    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        payload = json.dumps({"error": str(e), "done": True})
        yield f"data: {payload}\n\n"


@router.post("/chat")
async def chat(
    message: str = Form(...),
    session_id: str = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    """
    Send a message and optionally attach a file. Returns an SSE stream.

    - **message**: User's text message
    - **session_id**: Conversation session identifier (auto-generated if omitted)
    - **file**: Optional file attachment (PDF, DOCX, TXT, CSV, code files)
    """
    sid = session_id or str(uuid.uuid4())

    file_bytes = None
    filename = None
    file_content_type = None

    if file and file.filename:
        file_bytes = await file.read()
        if len(file_bytes) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum is {settings.localmind_max_file_size_mb} MB.",
            )
        filename = file.filename
        file_content_type = file.content_type or "application/octet-stream"

    return StreamingResponse(
        _event_stream(message, sid, file_bytes, filename, file_content_type),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-ID": sid,
        },
    )
