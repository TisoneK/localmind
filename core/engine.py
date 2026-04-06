"""
Core Engine — the central coordinator.

This is the single entry point for all user messages, regardless of surface
(Web UI or CLI). It orchestrates intent routing, tool dispatch, context
building, and streaming the model response.

Design rules:
- The engine knows nothing about the surface (no HTTP, no terminal code here)
- The engine calls tools by name through the registry — not directly
- All tool results flow through ToolResult — no raw strings
- History is written by the engine, not the surface
"""
from __future__ import annotations
import logging
from typing import AsyncIterator, Optional

from core.models import (
    Intent, Message, Role, EngineContext, StreamChunk,
    FileAttachment, ToolResult,
)
from core.config import settings
from core import intent_router, context_builder
from storage.db import SessionStore
from adapters import get_adapter
from tools import dispatch

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self):
        self._store = SessionStore(settings.localmind_db_path)
        self._adapter = get_adapter(settings.localmind_adapter)

    async def process(
        self,
        message: str,
        session_id: str,
        file: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Process a user message end-to-end and stream the response.

        Args:
            message: The user's text message.
            session_id: Identifies the conversation session.
            file: Optional raw file bytes if the user attached a file.
            filename: Original filename of the attachment.
            content_type: MIME type of the attachment.

        Yields:
            StreamChunk objects containing response text tokens.
        """
        # ── 1. Load session history ──────────────────────────────────────────
        history = self._store.get_history(session_id)

        # ── 2. Classify intent ───────────────────────────────────────────────
        has_attachment = file is not None
        intent = intent_router.classify(message, has_attachment=has_attachment)
        logger.info(f"[{session_id}] intent={intent.value} attachment={has_attachment}")

        # ── 3. Parse file attachment if present ──────────────────────────────
        file_attachment: Optional[FileAttachment] = None
        if file and filename:
            from tools.file_reader import parse_file
            file_attachment = await parse_file(
                data=file,
                filename=filename,
                content_type=content_type or "application/octet-stream",
                chunk_size=settings.localmind_chunk_size_tokens,
            )

        # ── 4. Dispatch tool if needed ───────────────────────────────────────
        tool_result: Optional[ToolResult] = None
        if intent not in (Intent.CHAT, Intent.FILE_TASK) or (
            intent == Intent.FILE_TASK and not has_attachment
        ):
            try:
                tool_result = await dispatch(intent, message)
            except Exception as e:
                logger.warning(f"Tool dispatch failed for {intent}: {e}")

        # ── 5. Build context ─────────────────────────────────────────────────
        ctx = EngineContext(
            session_id=session_id,
            message=message,
            intent=intent,
            history=history,
            tool_result=tool_result,
            file_attachment=file_attachment,
            memory_facts=[],  # v0.3: populated from memory tool
        )

        model_context_window = self._adapter.context_window
        prompt_messages = context_builder.build(ctx, model_context_window)

        # ── 6. Stream model response ─────────────────────────────────────────
        full_response = []
        async for chunk in self._adapter.chat(prompt_messages):
            full_response.append(chunk.text)
            yield chunk

        # ── 7. Persist exchange to history ───────────────────────────────────
        response_text = "".join(full_response)
        self._store.append(session_id, Message(role=Role.USER, content=message))
        self._store.append(session_id, Message(role=Role.ASSISTANT, content=response_text))
        logger.info(f"[{session_id}] response_tokens≈{len(response_text)//4}")
