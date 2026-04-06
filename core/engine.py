"""
Core Engine — v0.3

Pipeline:
  1.  Load history
  2.  LLM intent classification (rule-based fallback)
  3.  Tool scoring
  4.  Parse file attachment
  5.  Memory retrieval (4-factor scoring)
  6.  Initial tool dispatch
  7.  Build context
  7a. Direct stream (CHAT, FILE_TASK)
  7b. Agent loop (WEB_SEARCH, CODE_EXEC, FILE_WRITE, MEMORY_OP)
  8.  Secondary intent
  9.  Memory persistence
  10. History persistence + observability flush
"""
from __future__ import annotations
import logging
import time
from typing import AsyncIterator, Optional

from core.models import (
    Intent, Message, Role, EngineContext, StreamChunk,
    FileAttachment, ToolResult,
)
from core.config import settings
from core import context_builder
from core.intent_classifier import classify_with_llm
from core.memory import MemoryComposer
from core.agent import AgentLoop, AGENT_INTENTS
from core.tool_scorer import best_tool, score_tools
from core.obs import ObsCollector
from storage.db import SessionStore
from adapters import get_adapter
from tools import dispatch, available_tools

logger = logging.getLogger(__name__)


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
        obs: Optional[ObsCollector] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Process a user message end-to-end and stream chunks.
        Emits structured observability events into obs if provided.
        """
        _obs = obs or ObsCollector()  # no-op collector if caller doesn't care
        t0 = time.monotonic()

        # ── 1. History ────────────────────────────────────────────────────
        history = self._store.get_history(session_id)

        # ── 2. LLM Intent Classification ──────────────────────────────────
        has_attachment = file is not None
        primary_intent, secondary_intent, confidence = await classify_with_llm(
            message=message,
            has_attachment=has_attachment,
            adapter=self._adapter,
        )
        _obs.emit("intent_classified",
                  primary=primary_intent.value,
                  secondary=secondary_intent.value if secondary_intent else "none",
                  confidence=round(confidence, 2))

        # ── 3. Tool Scoring ────────────────────────────────────────────────
        tools = available_tools()
        scored = score_tools(tools, primary_intent, confidence)
        if scored:
            _obs.emit("tool_scored",
                      top=scored[0].intent.value,
                      score=round(scored[0].score, 3),
                      tools_evaluated=len(scored))

        top_tool = best_tool(tools, primary_intent, confidence)
        effective_intent = top_tool if top_tool and confidence < 0.6 else primary_intent

        # ── 4. File attachment ─────────────────────────────────────────────
        file_attachment: Optional[FileAttachment] = None
        if file and filename:
            from tools.file_reader import parse_file
            file_attachment = await parse_file(
                data=file,
                filename=filename,
                content_type=content_type or "application/octet-stream",
                chunk_size=settings.localmind_chunk_size_tokens,
            )

        # ── 5. Memory retrieval ────────────────────────────────────────────
        t_mem = time.monotonic()
        memory_facts = await self._memory.compose(
            query=message,
            intent=effective_intent,
            session_id=session_id,
        )
        _obs.emit("memory_retrieved",
                  facts=len(memory_facts),
                  latency_ms=round((time.monotonic() - t_mem) * 1000))

        # ── 6. Initial tool dispatch ───────────────────────────────────────
        tool_result: Optional[ToolResult] = None
        if effective_intent not in (Intent.CHAT, Intent.FILE_TASK) or (
            effective_intent == Intent.FILE_TASK and not has_attachment
        ):
            t_tool = time.monotonic()
            try:
                tool_result = await dispatch(effective_intent, message)
                _obs.emit("tool_dispatched",
                          tool=effective_intent.value,
                          success=tool_result is not None,
                          latency_ms=round((time.monotonic() - t_tool) * 1000))
            except Exception as e:
                logger.warning(f"Tool dispatch failed for {effective_intent}: {e}")
                _obs.emit("tool_failed", tool=effective_intent.value, error=str(e)[:80])

        # ── 7. Build context ───────────────────────────────────────────────
        ctx = EngineContext(
            session_id=session_id,
            message=message,
            intent=effective_intent,
            history=history,
            tool_result=tool_result,
            file_attachment=file_attachment,
            memory_facts=memory_facts,
        )
        prompt_messages = context_builder.build(ctx, self._adapter.context_window)

        # ── 7a/7b. Stream response ─────────────────────────────────────────
        full_response = []

        if effective_intent in AGENT_INTENTS:
            _obs.emit("agent_loop_start", intent=effective_intent.value)
            async for chunk in self._agent_loop.run(
                messages=prompt_messages,
                intent=effective_intent,
                initial_tool_result=tool_result,
                available_tools=tools,
                confidence=confidence,
            ):
                full_response.append(chunk.text)
                yield chunk
        else:
            async for chunk in self._adapter.chat(prompt_messages):
                full_response.append(chunk.text)
                yield chunk

        response_text = "".join(full_response)

        # ── 8. Secondary intent ────────────────────────────────────────────
        if secondary_intent == Intent.FILE_WRITE and tool_result:
            try:
                from tools.file_writer import write_response
                await write_response(message=message, content=response_text)
            except Exception as e:
                logger.warning(f"Secondary file write failed: {e}")

        # ── 9. Memory persistence ──────────────────────────────────────────
        if effective_intent == Intent.MEMORY_OP:
            await self._extract_and_store_facts(message, session_id, _obs)

        # ── 10. Persist history ────────────────────────────────────────────
        self._store.append(session_id, Message(role=Role.USER, content=message))
        self._store.append(session_id, Message(role=Role.ASSISTANT, content=response_text))

        total_ms = round((time.monotonic() - t0) * 1000)
        _obs.emit("turn_complete",
                  intent=effective_intent.value,
                  confidence=round(confidence, 2),
                  tokens_approx=len(response_text) // 4,
                  total_latency_ms=total_ms,
                  memory_facts=len(memory_facts),
                  agent_mode=effective_intent in AGENT_INTENTS)

    async def _extract_and_store_facts(self, message: str, session_id: str, obs: ObsCollector) -> None:
        import re
        fact = re.sub(
            r"^\s*(remember|note|store|keep in mind)\s*(that\s*)?",
            "", message, flags=re.IGNORECASE,
        ).strip()
        if fact:
            importance = 0.8 if any(w in message.lower() for w in ["prefer", "always", "never", "important"]) else 0.5
            stored = await self._memory.store(
                fact=fact, session_id=session_id,
                memory_type="semantic", source="user", importance=importance,
            )
            if stored:
                obs.emit("memory_stored", fact_preview=fact[:60], importance=importance)
