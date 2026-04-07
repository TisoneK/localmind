"""
Chat endpoint — v0.3

POST /api/chat — multipart/form-data with message + optional file.
Streams SSE. Interleaves observability events between text chunks.

SSE payload types emitted in order:
    {"obs_event": {"type": "intent_classified", "data": {...}}}
    {"intent": "web_search", "confidence": 0.92}   ← convenience extract
    {"text": "Hello", "done": false}
    ...
    {"text": "", "done": true, "file_path": "/path/if/written", "file_name": "foo.py"}
    {"obs_event": {"type": "turn_complete", "data": {...}}}
"""
from __future__ import annotations
import json
import uuid
import logging
from pathlib import Path

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
    file_full_path=None,   # full path sent by UI if available
):
    obs = ObsCollector()
    intent_emitted = False
    file_path_written = None
    file_name_written = None

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
                if evt.type == "intent_classified" and not intent_emitted:
                    intent_emitted = True
                    yield _sse({
                        "intent": evt.data.get("primary"),
                        "confidence": float(evt.data.get("confidence", 0.5)),
                    })
                # Capture file write metadata for the done event
                if evt.type == "tool_dispatched" and evt.data.get("tool") == "file_write":
                    pass  # path comes through chunk metadata below

            if chunk.error:
                yield _sse({"error": chunk.error, "done": True})
                return

            # Capture file path from tool result metadata streamed in chunk
            if hasattr(chunk, "metadata") and chunk.metadata:
                if "path" in chunk.metadata:
                    file_path_written = chunk.metadata["path"]
                    file_name_written = chunk.metadata.get("filename")

            if chunk.done:
                done_payload: dict = {"text": "", "done": True}
                if file_path_written:
                    done_payload["file_path"] = file_path_written
                    done_payload["file_name"] = file_name_written or Path(file_path_written).name
                yield _sse(done_payload)
            else:
                yield _sse({"text": chunk.text, "done": False})

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
    file_full_path: Optional[str] = Form(default=None),  # full path from UI
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
        # Save uploaded file to uploads dir
        uploads_dir = Path(settings.localmind_uploads_path)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        dest = uploads_dir / file.filename
        dest.write_bytes(file_bytes)
        filename = file.filename
        file_content_type = file.content_type or "application/octet-stream"
        # Use client-provided full path if given, otherwise use uploads dir path
        if not file_full_path:
            file_full_path = str(dest)

    return StreamingResponse(
        _event_stream(message, sid, file_bytes, filename, file_content_type, file_full_path),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-ID": sid,
        },
    )
