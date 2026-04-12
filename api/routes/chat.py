"""
Chat endpoint — v0.4

POST /api/chat — multipart/form-data with message + optional file.
Streams SSE.

Title generation (two-stage):
  - Stage 1 (sync): heuristic placeholder written immediately on first turn
  - Stage 2 (async): LLM-generated title written ~2s later as background task

SSE payload types:
    {"obs_event": {"type": "intent_classified", "data": {...}}}
    {"intent": "web_search", "confidence": 0.92}
    {"text": "Hello", "done": false}
    {"text": "", "done": true, "file_path": "...", "file_name": "..."}
    {"obs_event": {"type": "turn_complete", "data": {...}}}
"""
from __future__ import annotations
import asyncio
import json
import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional

from core.engine import Engine
from core.obs import ObsCollector
from core.config import settings
from core.title_generator import generate_title_smart, refine_title_async

router = APIRouter()
logger = logging.getLogger(__name__)

_engine = Engine()
MAX_FILE_BYTES = settings.localmind_max_file_size_mb * 1024 * 1024


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _is_first_turn(session_id: str) -> bool:
    """True if the session has no messages yet (this is the opening turn)."""
    try:
        from storage.db import SessionStore
        store = SessionStore(settings.localmind_db_path)
        title = store.get_session_title(session_id)
        # A session with no title hasn't had its first turn processed yet
        return title is None
    except Exception:
        return False


async def _event_stream(
    message: str,
    session_id: str,
    is_first: bool,
    file_bytes=None,
    filename=None,
    content_type=None,
    original_path=None,
    disconnect=None,
):
    obs = ObsCollector()
    file_path_written = None
    file_name_written = None

    # ── Stage 1: write heuristic placeholder title immediately ────────────
    # This ensures the sidebar shows something before the LLM title arrives.
    if is_first:
        try:
            from storage.db import SessionStore
            store = SessionStore(settings.localmind_db_path)
            placeholder = generate_title_smart(message)
            store.update_session_title(session_id, placeholder)
            logger.info(f"[title] placeholder '{placeholder}' for {session_id[:8]}")
        except Exception as exc:
            logger.warning(f"[title] placeholder write failed: {exc}")

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
            if disconnect and await disconnect():
                logger.info(f"Client disconnected for session {session_id}")
                break

            # Flush buffered obs events before each text chunk
            for evt in obs.drain():
                yield _sse(evt.to_sse_dict())
                if evt.type == "intent_classified":
                    yield _sse({
                        "intent":     evt.data.get("primary"),
                        "confidence": float(evt.data.get("confidence", 0.5)),
                    })

            if hasattr(chunk, "metadata") and chunk.metadata:
                obs_evt = chunk.metadata.get("obs_event")
                if obs_evt:
                    yield _sse(obs_evt.to_sse_dict())
                if "path" in chunk.metadata:
                    file_path_written = chunk.metadata["path"]
                    file_name_written = chunk.metadata.get("filename")

            if chunk.error:
                if chunk.text:
                    yield _sse({"text": chunk.text, "done": False})
                yield _sse({"error": chunk.error, "done": True})
                return

            if chunk.done:
                done_payload: dict = {"text": "", "done": True}
                if file_path_written:
                    done_payload["file_path"] = file_path_written
                    done_payload["file_name"] = file_name_written or Path(file_path_written).name
                yield _sse(done_payload)
            elif chunk.text:
                yield _sse({"text": chunk.text, "done": False})

        # Final obs flush
        for evt in obs.drain():
            yield _sse(evt.to_sse_dict())

    except Exception as exc:
        logger.error(f"[chat] stream error: {exc}", exc_info=True)
        yield _sse({"error": str(exc), "done": True})

    # ── Stage 2: schedule LLM title as background task ────────────────────
    # Fires after the response stream completes so it never blocks the user.
    if is_first:
        asyncio.create_task(refine_title_async(session_id, message))
        logger.info(f"[title] LLM refinement scheduled for {session_id[:8]}")


@router.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    file_full_path: Optional[str] = Form(default=None),
    original_path: Optional[str] = Form(default=None),
):
    """Stream a chat response as Server-Sent Events."""
    sid = session_id or str(uuid.uuid4())
    is_first = _is_first_turn(sid)

    file_bytes = None
    filename = None
    file_content_type = None

    if file and file.filename:
        try:
            file_bytes = await file.read()
            if len(file_bytes) > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum {settings.localmind_max_file_size_mb} MB.",
                )
            uploads_dir = Path(settings.localmind_uploads_path)
            uploads_dir.mkdir(parents=True, exist_ok=True)
            dest = uploads_dir / file.filename
            dest.write_bytes(file_bytes)
            filename = file.filename
            file_content_type = file.content_type or "application/octet-stream"
            if not file_full_path:
                file_full_path = str(dest)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"[chat] file upload error: {exc}", exc_info=True)
            async def _err():
                yield _sse({"error": f"File upload failed: {exc}", "done": True})
            return StreamingResponse(
                _err(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    return StreamingResponse(
        _event_stream(
            message, sid, is_first,
            file_bytes, filename, file_content_type,
            original_path or file_full_path,
            disconnect=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-ID": sid,
        },
    )
