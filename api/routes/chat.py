"""
Chat endpoint — v0.3

POST /api/chat — multipart/form-data with message + optional file.
Streams SSE. Interleaves observability events between text chunks.

SSE payload types emitted in order:
    {"obs_event": {"type": "intent_classified", "data": {...}}}
    {"intent": "web_search", "confidence": 0.92}   ← convenience extract
    {"text": "Hello", "done": false}
    ...
    {"text": "", "done": true}
    {"obs_event": {"type": "turn_complete", "data": {...}}}
"""
from __future__ import annotations
import json
import uuid
import logging

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional

from core.engine import Engine
from core.obs import ObsCollector
from core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

_engine = Engine()
MAX_FILE_BYTES = settings.localmind_max_file_size_mb * 1024 * 1024


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _event_stream(
    message: str,
    session_id: str,
    file_bytes=None,
    filename=None,
    content_type=None,
):
    obs = ObsCollector()
    intent_emitted = False

    try:
        async for chunk in _engine.process(
            message=message,
            session_id=session_id,
            file=file_bytes,
            filename=filename,
            content_type=content_type,
            obs=obs,
        ):
            # Flush any buffered obs events before each text chunk
            for evt in obs.drain():
                yield _sse(evt.to_sse_dict())
                # Convenience: extract intent + confidence once
                if evt.type == "intent_classified" and not intent_emitted:
                    intent_emitted = True
                    yield _sse({
                        "intent": evt.data.get("primary"),
                        "confidence": float(evt.data.get("confidence", 0.5)),
                    })

            if chunk.error:
                yield _sse({"error": chunk.error, "done": True})
                return
            yield _sse({"text": chunk.text, "done": chunk.done})

        # Final obs flush (turn_complete lands here)
        for evt in obs.drain():
            yield _sse(evt.to_sse_dict())

    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        yield _sse({"error": str(e), "done": True})


@router.post("/chat")
async def chat(
    message: str = Form(...),
    session_id: str = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    """Stream a chat response as Server-Sent Events."""
    sid = session_id or str(uuid.uuid4())

    file_bytes = None
    filename = None
    file_content_type = None

    if file and file.filename:
        file_bytes = await file.read()
        if len(file_bytes) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum {settings.localmind_max_file_size_mb} MB.",
            )
        filename = file.filename
        file_content_type = file.content_type or "application/octet-stream"

    return StreamingResponse(
        _event_stream(message, sid, file_bytes, filename, file_content_type),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-ID": sid,
        },
    )
