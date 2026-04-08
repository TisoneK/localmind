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
    FileAttachment, ToolResult, RiskLevel,
)
from core.config import settings
from core import context_builder
from core.summarizer import maybe_compress_history
from core.intent_classifier import classify_with_llm
from core.memory import MemoryComposer
from core.agent import AgentLoop, AGENT_INTENTS
from core.tool_scorer import best_tool, score_tools, load_reliability_from_db, record_tool_outcome
from core.obs import ObsCollector
from storage.db import SessionStore
from adapters import get_adapter
from tools import dispatch, available_tools

logger = logging.getLogger(__name__)


from core.model_router import best_model_for, update_pulled_models


class Engine:
    def __init__(self):
        self._store = SessionStore(settings.localmind_db_path)
        self._adapter = get_adapter(settings.localmind_adapter)
        self._memory = MemoryComposer()
        self._agent_loop = AgentLoop(adapter=self._adapter)
        # Bootstrap tool reliability from historical DB stats
        try:
            load_reliability_from_db(self._store.get_reliability())
        except Exception as e:
            logger.debug(f"[engine] reliability bootstrap skipped: {e}")
        # Bootstrap model router with currently pulled models
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # If there's already a running loop, create a task
                asyncio.create_task(self._refresh_model_router())
            except RuntimeError:
                # No running loop, we can run it directly
                asyncio.run(self._refresh_model_router())
        except Exception as e:
            logger.debug(f"[engine] model router bootstrap skipped: {e}")

    async def _refresh_model_router(self) -> None:
        """Refresh the model router's knowledge of pulled models."""
        try:
            pulled = await self._adapter.list_models()
            update_pulled_models(pulled)
        except Exception as e:
            logger.debug(f"[engine] model router refresh failed: {e}")

    def _adapter_for(self, intent: Intent):
        """Return an adapter configured with the best model for this intent."""
        model = best_model_for(intent, fallback=settings.ollama_model)
        if model == settings.ollama_model:
            return self._adapter  # reuse existing adapter — no extra allocation
        return get_adapter(settings.localmind_adapter, model_override=model)

    async def process(
        self,
        message: str,
        session_id: str,
        file: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        obs: Optional[ObsCollector] = None,
        original_path: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Process a user message end-to-end and stream chunks.
        Emits structured observability events into obs if provided.
        """
        _obs = obs or ObsCollector()  # no-op collector if caller doesn't care
        t0 = time.monotonic()

        # ── 0. Safety gate ────────────────────────────────────────────────
        from core.safety_gate import check as safety_check
        is_safe, gate_reason = safety_check(message)
        if not is_safe:
            _obs = obs or ObsCollector()
            _obs.emit("safety_blocked", reason=gate_reason[:80])
            yield StreamChunk(text=gate_reason, done=False)
            yield StreamChunk(text="", done=True)
            return

        # ── 1. History ────────────────────────────────────────────────────
        history = self._store.get_history(session_id)

        # ── 1a. Fast-path: obvious CHAT messages bypass the full pipeline ──
        # The rule-based router can already confirm CHAT with high confidence
        # for greetings, short replies, and unambiguous conversational messages.
        # Skip classifier LLM call, memory embed, tool dispatch for these.
        has_attachment = file is not None
        from core import intent_router as _router
        _fast_primary, _fast_secondary = _router.classify_multi(message, has_attachment)
        if _fast_primary == Intent.CHAT and not has_attachment:
            from core.intent_classifier import _RULE_CONFIDENCE_BY_INTENT
            _fast_conf = _RULE_CONFIDENCE_BY_INTENT.get(Intent.CHAT.value, 0.85)
            _obs.emit("intent_classified", primary="chat", secondary="none", confidence=_fast_conf)
            ctx = EngineContext(
                session_id=session_id,
                message=message,
                intent=Intent.CHAT,
                history=history,
                tool_result=None,
                file_attachment=None,
                memory_facts=[],
            )
            prompt_messages = context_builder.build(ctx, self._adapter.context_window)
            full_response = []
            async for chunk in self._adapter.chat(prompt_messages):
                full_response.append(chunk.text)
                yield chunk
            response_text = "".join(full_response)
            self._store.append(session_id, Message(role=Role.USER, content=message))
            self._store.append(session_id, Message(role=Role.ASSISTANT, content=response_text))
            _obs.emit("turn_complete", intent="chat", confidence=_fast_conf,
                      tokens_approx=len(response_text) // 4,
                      total_latency_ms=round((time.monotonic() - t0) * 1000),
                      memory_facts=0, agent_mode=False)
            return

        # ── 2. LLM Intent Classification ──────────────────────────────────
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
                original_path=original_path,  # Pass original path for in-place processing
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

                # ── Permission gate short-circuit ──────────────────────────
                # If tool requires user confirmation before proceeding,
                # stream the gate event and stop — do not call the LLM.
                if tool_result and tool_result.requires_confirmation:
                    _obs.emit("permission_required",
                              tool=effective_intent.value,
                              pending_path=tool_result.metadata.get("pending_path", ""),
                              pending_content=tool_result.metadata.get("pending_content", ""),
                              filename=tool_result.metadata.get("filename", ""))
                    yield StreamChunk(text=tool_result.content, done=False)
                    yield StreamChunk(text="", done=True)
                    # Store user message but not assistant — gate hasn't resolved
                    self._store.append(session_id, Message(role=Role.USER, content=message))
                    return

                tool_ok = (
                    tool_result is not None
                    and tool_result.content.strip()
                    and tool_result.content.strip() != "No results found."
                )
                _obs.emit("tool_dispatched",
                          tool=effective_intent.value,
                          success=tool_ok,
                          latency_ms=round((time.monotonic() - t_tool) * 1000))
                record_tool_outcome(self._store, effective_intent.value, success=tool_ok,
                                    latency_ms=round((time.monotonic() - t_tool) * 1000))
                if not tool_ok:
                    logger.warning(f"[engine] tool {effective_intent.value} returned empty/failed result - using error fallback")
                    # For web search failures, provide a clear error message instead of hallucinating
                    if effective_intent == Intent.WEB_SEARCH:
                        tool_result = ToolResult(
                            content="I'm unable to search the web right now. This could be due to:\n- No internet connection\n- Search service temporarily unavailable\n- DuckDuckGo/Brave API issues\n\nPlease try again later or check your internet connection.",
                            risk=RiskLevel.LOW,
                            source="web_search_error"
                        )
                    else:
                        tool_result = None
                        effective_intent = Intent.CHAT
            except Exception as e:
                logger.warning(f"Tool dispatch failed for {effective_intent}: {e}")
                _obs.emit("tool_failed", tool=effective_intent.value, error=str(e)[:80])
                record_tool_outcome(self._store, effective_intent.value, success=False,
                                    latency_ms=round((time.monotonic() - t_tool) * 1000))
                effective_intent = Intent.CHAT

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

        # Pick the best available model for this intent
        intent_adapter = self._adapter_for(effective_intent)
        prompt_messages = context_builder.build(ctx, intent_adapter.context_window)

        # Compress history if approaching context window limit
        prompt_messages = await maybe_compress_history(
            prompt_messages, intent_adapter, intent_adapter.context_window
        )

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
                adapter=intent_adapter,
            ):
                full_response.append(chunk.text)
                yield chunk
        else:
            async for chunk in intent_adapter.chat(prompt_messages):
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
        if not fact:
            return

        # ── Negative learning gate ────────────────────────────────────────
        # Block facts that could corrupt reasoning, override safety, or teach
        # the model to lie, be harmful, or behave against its values.
        _BLOCKED_PATTERNS = [
            # Override identity / safety instructions
            r"\b(ignore|bypass|override|disable|forget)\b.{0,30}\b(safety|rules|guidelines|instructions|system prompt|restrictions)\b",
            r"\byou (are|must|should|will)\b.{0,40}\b(lie|deceive|pretend|ignore|always agree|never refuse)\b",
            r"\b(always|never) (tell|say|respond|answer|agree|refuse)\b",
            r"\bact (as|like)\b.{0,30}\b(jailbreak|unrestricted|dan|evil|unfiltered)\b",
            # Negative self-image / harmful self-talk
            r"\byou (are|were) (wrong|stupid|dumb|useless|broken|terrible|awful|bad)\b",
            r"\b(hate|despise|dislike) (you|yourself|itself)\b",
            # Prompt injection patterns
            r"(system|assistant|user)\s*:\s*(ignore|disregard|forget)",
            r"<\s*(system|instruction|prompt)\s*>",
            # Deception training
            r"\b(lie|make up|fabricate|hallucinate)\b.{0,20}\b(answers?|results?|facts?|data)\b",
        ]
        fact_lower = fact.lower()
        for pattern in _BLOCKED_PATTERNS:
            if re.search(pattern, fact_lower, re.IGNORECASE):
                logger.warning(f"[memory gate] blocked harmful fact: {fact[:80]}")
                obs.emit("memory_blocked", reason="negative_learning_gate", preview=fact[:60])
                return

        importance = 0.8 if any(w in message.lower() for w in ["prefer", "always", "never", "important"]) else 0.5
        stored = await self._memory.store(
            fact=fact, session_id=session_id,
            memory_type="semantic", source="user", importance=importance,
        )
        if stored:
            obs.emit("memory_stored", fact_preview=fact[:60], importance=importance)
