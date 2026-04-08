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

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional
from contextlib import asynccontextmanager

from core.engine import Engine
from core.obs import ObsCollector
from core.config import settings
from core.title_generator import generate_title_smart, should_generate_title, refine_title_async

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
    original_path=None,    # original file location
    disconnect=None,
):
    obs = ObsCollector()
    intent_emitted = False
    file_path_written = None
    file_name_written = None
    captured_intent = None

    try:
        async for chunk in _engine.process(
            message=message,
            session_id=session_id,
            file=file_bytes,
            filename=filename,
            content_type=content_type,
            obs=obs,
            original_path=original_path,
        ):
            # Check if client disconnected
            if disconnect and await disconnect():
                logger.info(f"Client disconnected, stopping stream for session {session_id}")
                break
            # Flush any buffered obs events before each text chunk
            for evt in obs.drain():
                yield _sse(evt.to_sse_dict())
                if evt.type == "intent_classified" and not intent_emitted:
                    intent_emitted = True
                    captured_intent = evt.data.get("primary")
                    
                    # Update title with intent-aware generation
                    try:
                        from storage.db import SessionStore
                        store = SessionStore(settings.localmind_db_path)
                        current_title = store.get_session_title(session_id)
                        
                        # Always update if we have a better smart title
                        smart_title = generate_title_smart(message, captured_intent)
                        
                        # Only update if different or if current title looks like truncation
                        should_update = (
                            not current_title or 
                            current_title == "New Chat" or
                            len(current_title) > 35 or  # Likely truncated
                            current_title.endswith("...") or  # Truncated
                            current_title != smart_title
                        )
                        
                        if should_update:
                            store.update_session_title(session_id, smart_title)
                            logger.info(f"INTENT-AWARE UPDATE: {captured_intent} -> '{smart_title}' (replaced: '{current_title}')")
                    except Exception as e:
                        logger.warning(f"Failed to update title with intent: {e}")
                    
                    yield _sse({
                        "intent": evt.data.get("primary"),
                        "confidence": float(evt.data.get("confidence", 0.5)),
                    })
                if evt.type == 'tool_dispatched' and not intent_emitted:
                    intent_emitted = True

            # Process each chunk and capture observability events
            if hasattr(chunk, 'metadata') and chunk.metadata:
                # Chunk contains observability data from agent
                obs_evt = chunk.metadata.get('obs_event')
                if obs_evt:
                    yield _sse(obs_evt.to_sse_dict())

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
            elif chunk.text:
                logger.debug(f"[chat] yielding text chunk: '{chunk.text}' (done=False)")
                yield _sse({"text": chunk.text, "done": False})

        # Final obs flush (turn_complete lands here)
        for evt in obs.drain():
            yield _sse(evt.to_sse_dict())

        # Fallback: If no intent was classified, still generate smart title
        if not intent_emitted:
            try:
                from storage.db import SessionStore
                store = SessionStore(settings.localmind_db_path)
                current_title = store.get_session_title(session_id)
                
                should_update = (
                    not current_title or 
                    current_title == "New Chat" or
                    len(current_title) > 35 or
                    current_title.endswith("...")
                )
                
                if should_update:
                    smart_title = generate_title_smart(message, None)  # No intent available
                    store.update_session_title(session_id, smart_title)
                    logger.info(f"FALLBACK TITLE UPDATE: '{smart_title}' (replaced: '{current_title}')")
            except Exception as e:
                logger.warning(f"Failed to update fallback title: {e}")

    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        yield _sse({"error": str(e), "done": True})


@router.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    file_full_path: Optional[str] = Form(default=None),  # full path from UI
    original_path: Optional[str] = Form(default=None),  # original file location
):
    """Stream a chat response as Server-Sent Events."""
    logger.info(f"Chat request: message='{message}', session_id='{session_id}'")
    logger.info(f"File received: {file is not None}, filename: {file.filename if file else 'None'}")
    logger.info(f"File full path: {file_full_path}")
    
    sid = session_id or str(uuid.uuid4())

    # 🔥 Title generation will happen during intent classification in the event stream
    # This ensures we have access to the detected intent for smart title generation

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
        _event_stream(
            message, sid, file_bytes, filename, file_content_type, file_full_path, original_path,
            disconnect=request.is_disconnected
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-ID": sid,
        },
    )
