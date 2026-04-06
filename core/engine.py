"""
Core Engine — the central coordinator.

v0.2 upgrades:
  1. Memory is LIVE — MemoryComposer retrieves relevant facts every call
  2. Agent loop for complex intents (WEB_SEARCH, CODE_EXEC, FILE_WRITE, MEMORY_OP)
  3. Multi-intent support via classify_multi()
  4. MEMORY_OP intent writes facts to the vector store after responding
  5. Model router stub — logs routing decision, groundwork for cost-aware selection

Design rules (unchanged):
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
from core.memory import MemoryComposer
from core.agent import AgentLoop, AGENT_INTENTS
from storage.db import SessionStore
from adapters import get_adapter
from tools import dispatch, list_tools

logger = logging.getLogger(__name__)


def _select_model(intent: Intent, message: str) -> str:
    """
    Model router — select the best adapter/model for this request.

    v0.2: logs the decision. v0.3: actually switches the adapter.
    Logic:
        CHAT + short message  → small/fast model (e.g. phi3:mini)
        CODE_EXEC             → code-specialized model (e.g. qwen2.5-coder)
        anything else         → default configured model
    """
    model = settings.ollama_model
    word_count = len(message.split())

    if intent == Intent.CHAT and word_count < 30:
        routed = getattr(settings, "ollama_model_fast", model)
    elif intent == Intent.CODE_EXEC:
        routed = getattr(settings, "ollama_model_code", model)
    else:
        routed = model

    if routed != model:
        logger.info(f"[model router] intent={intent.value} words={word_count} → {routed} (default: {model})")
    return routed


class Engine:
    def __init__(self):
        self._store = SessionStore(settings.localmind_db_path)
        self._adapter = get_adapter(settings.localmind_adapter)
        self._memory = MemoryComposer()
        self._agent_loop = AgentLoop(adapter=self._adapter)

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

        Pipeline (v0.2):
          1. Load session history
          2. Classify intent (primary + secondary)
          3. Parse file attachment
          4. Retrieve memory facts (LIVE — was always [] before)
          5. Dispatch initial tool
          6. Build context
          7a. Simple intents → stream model directly
          7b. Complex intents → run agent loop
          8. Handle secondary intent (e.g. write tool result to file)
          9. Persist memory facts for MEMORY_OP
         10. Persist exchange to history
        """
        # ── 1. Load session history ──────────────────────────────────────────
        history = self._store.get_history(session_id)

        # ── 2. Classify intent (primary + optional secondary) ────────────────
        has_attachment = file is not None
        primary_intent, secondary_intent = intent_router.classify_multi(
            message, has_attachment=has_attachment
        )
        logger.info(
            f"[{session_id}] intent={primary_intent.value} "
            f"secondary={secondary_intent.value if secondary_intent else 'none'} "
            f"attachment={has_attachment}"
        )

        # Model routing decision (logged; v0.3 will switch adapters)
        _select_model(primary_intent, message)

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

        # ── 4. Retrieve memory facts (LIVE) ──────────────────────────────────
        memory_facts = await self._memory.compose(
            query=message,
            intent=primary_intent,
            session_id=session_id,
        )
        if memory_facts:
            logger.info(f"[{session_id}] memory_facts={len(memory_facts)} retrieved")

        # ── 5. Dispatch initial tool ─────────────────────────────────────────
        tool_result: Optional[ToolResult] = None
        if primary_intent not in (Intent.CHAT, Intent.FILE_TASK) or (
            primary_intent == Intent.FILE_TASK and not has_attachment
        ):
            try:
                tool_result = await dispatch(primary_intent, message)
            except Exception as e:
                logger.warning(f"Tool dispatch failed for {primary_intent}: {e}")

        # ── 6. Build context ─────────────────────────────────────────────────
        ctx = EngineContext(
            session_id=session_id,
            message=message,
            intent=primary_intent,
            history=history,
            tool_result=tool_result,
            file_attachment=file_attachment,
            memory_facts=memory_facts,  # ← NOW POPULATED
        )

        model_context_window = self._adapter.context_window
        prompt_messages = context_builder.build(ctx, model_context_window)

        # ── 7. Stream response ───────────────────────────────────────────────
        full_response = []

        if primary_intent in AGENT_INTENTS:
            # 7b. Agent loop for complex intents
            logger.info(f"[{session_id}] entering agent loop for intent={primary_intent.value}")
            async for chunk in self._agent_loop.run(
                messages=prompt_messages,
                intent=primary_intent,
                initial_tool_result=tool_result,
                available_tools=list_tools(),
            ):
                full_response.append(chunk.text)
                yield chunk
        else:
            # 7a. Single-pass for CHAT and FILE_TASK
            async for chunk in self._adapter.chat(prompt_messages):
                full_response.append(chunk.text)
                yield chunk

        response_text = "".join(full_response)

        # ── 8. Handle secondary intent ───────────────────────────────────────
        if secondary_intent == Intent.FILE_WRITE and tool_result:
            try:
                from tools.file_writer import write_response
                await write_response(message=message, content=response_text)
                logger.info(f"[{session_id}] secondary FILE_WRITE completed")
            except Exception as e:
                logger.warning(f"Secondary file write failed: {e}")

        # ── 9. Persist extracted facts for memory ops ────────────────────────
        if primary_intent == Intent.MEMORY_OP:
            await self._extract_and_store_facts(message, session_id)

        # ── 10. Persist exchange to history ──────────────────────────────────
        self._store.append(session_id, Message(role=Role.USER, content=message))
        self._store.append(session_id, Message(role=Role.ASSISTANT, content=response_text))
        logger.info(f"[{session_id}] response_tokens≈{len(response_text)//4}")

    async def _extract_and_store_facts(self, message: str, session_id: str) -> None:
        """
        Extract facts from a MEMORY_OP message and store them.

        Simple extraction: the message itself is the fact.
        v0.3: use the model to extract structured facts from the response.
        """
        # Strip the trigger words to get the core fact
        import re
        fact = re.sub(
            r"^\s*(remember|note|store|save|keep in mind)\s*(that\s*)?",
            "",
            message,
            flags=re.IGNORECASE,
        ).strip()
        if fact:
            stored = await self._memory.store(
                fact=fact,
                session_id=session_id,
                memory_type="semantic",
                source="user",
            )
            if stored:
                logger.info(f"[{session_id}] stored memory fact: {fact[:60]}")
