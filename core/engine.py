"""
Core Engine — v0.4

Pipeline:
  1.  Load history
  2.  LLM intent classification (rule-based fallback)
  3.  Tool scoring
  4.  Parse file attachment
  5.  Memory retrieval (4-factor scoring)
  6.  Initial tool dispatch (with retry budget)
  7.  Build context
  7a. Direct stream (CHAT, FILE_TASK)
  7b. Agent loop (WEB_SEARCH, CODE_EXEC, FILE_WRITE, MEMORY_OP)
  8.  Secondary intent
  9.  Memory persistence (with deduplication gate)
  10. History persistence + observability flush

Improvements over v0.3:
- Async startup() method replaces fragile asyncio bootstrap in __init__
- _filter_system_leaks imported once at module level (no inline re-imports)
- Tool output injection filtering before entering LLM context
- Streaming response filtered at chunk level, not post-hoc after full send
- Intent history window tracked per session, fed into AgentLoop
- Post-response memory update: assistant answers can generate new facts
- Memory deduplication gate blocks near-duplicate fact storage
- Observability aggregation: p50/p95 latency + tool success rates via MetricsStore
- effective_intent resolved once via _resolve_intent(), no mid-pipeline mutation
- Secondary intent handler extended to a match block with logged fallthrough
- Token approximation uses char-based heuristic with script-aware fallback note
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
from core.metrics import MetricsStore  # NEW: aggregated p50/p95 per intent + tool success rates
from core.filters import _filter_system_leaks, _filter_tool_injection, _filter_code_output
from core.model_router import best_model_for, update_pulled_models
from storage.db import SessionStore
from adapters import get_adapter
from tools import dispatch, available_tools

logger = logging.getLogger(__name__)

# Max entries kept in per-session intent history window fed to AgentLoop
INTENT_HISTORY_WINDOW = 5

# Similarity threshold for memory deduplication (0–1, higher = stricter)
MEMORY_DEDUP_THRESHOLD = 0.85


def _resolve_intent(
    primary_intent: Intent,
    top_tool: Optional[Intent],
    confidence: float,
) -> Intent:
    """
    Single, explicit intent resolution. Replaces mid-pipeline effective_intent mutation.
    If confidence is low and a scored tool disagrees with the classifier, defer to the tool.
    """
    if top_tool and confidence < 0.6:
        logger.debug(f"[engine] low confidence ({confidence:.2f}), deferring to top_tool={top_tool.value}")
        return top_tool
    return primary_intent


def _approx_tokens(text: str) -> int:
    """
    Rough token count. ~4 chars/token for Latin scripts; ~1.5 for CJK/Arabic.
    Not used for billing — observability only.
    """
    cjk_and_arabic = sum(
        1 for c in text
        if '\u4e00' <= c <= '\u9fff'      # CJK Unified
        or '\u0600' <= c <= '\u06ff'      # Arabic
        or '\uac00' <= c <= '\ud7a3'      # Hangul
    )
    ratio = 1.5 if cjk_and_arabic > len(text) * 0.2 else 4.0
    return max(1, round(len(text) / ratio))


class Engine:
    def __init__(self):
        self._store = SessionStore(settings.localmind_db_path)
        self._adapter = get_adapter(settings.localmind_adapter)
        self._memory = MemoryComposer()
        self._agent_loop = AgentLoop(adapter=self._adapter)
        self._metrics = MetricsStore()
        # Per-session intent history: {session_id: [intent_value, ...]}
        self._intent_history: dict[str, list[str]] = {}
        # Reliability bootstrap happens in startup(), not here
        try:
            load_reliability_from_db(self._store.get_reliability())
        except Exception as e:
            logger.debug(f"[engine] reliability bootstrap skipped: {e}")

    async def startup(self) -> None:
        """
        Async initialization. Call this from your app lifespan / startup hook.
        Replaces the fragile asyncio.run() / create_task() in __init__.

        Example (FastAPI):
            @app.on_event("startup")
            async def on_startup():
                await engine.startup()
        """
        try:
            pulled = await self._adapter.list_models()
            update_pulled_models(pulled)
            logger.info(f"[engine] model router refreshed: {len(pulled)} models available")
        except Exception as e:
            logger.warning(f"[engine] model router refresh failed (non-fatal): {e}")

    def _adapter_for(self, intent: Intent):
        """Return an adapter configured with the best model for this intent."""
        model = best_model_for(intent, fallback=settings.ollama_model)
        if model == settings.ollama_model:
            return self._adapter
        return get_adapter(settings.localmind_adapter, model_override=model)

    def _update_intent_history(self, session_id: str, intent: Intent) -> list[str]:
        """Track a rolling window of intent values per session."""
        history = self._intent_history.setdefault(session_id, [])
        history.append(intent.value)
        if len(history) > INTENT_HISTORY_WINDOW:
            history.pop(0)
        return list(history)

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
        _obs = obs or ObsCollector()
        t0 = time.monotonic()

        # ── 0. Safety gate ────────────────────────────────────────────────
        from core.safety_gate import check as safety_check
        is_safe, gate_reason = safety_check(message)
        if not is_safe:
            _obs.emit("safety_blocked", reason=gate_reason[:80])
            yield StreamChunk(text=gate_reason, done=False)
            yield StreamChunk(text="", done=True)
            return

        # ── 1. History ────────────────────────────────────────────────────
        history = self._store.get_history(session_id)

        # ── 1a. Fast-path: obvious CHAT bypasses full pipeline ─────────────
        has_attachment = file is not None
        from core import intent_router as _router
        _fast_primary, _fast_secondary = _router.classify_multi(message, has_attachment)

        # Memory-hint check: bypass fast-path if message likely references past context
        _memory_hint_words = {"earlier", "before", "last time", "you said", "remember", "told you", "previous"}
        _has_memory_hint = any(w in message.lower() for w in _memory_hint_words)

        print(f"[DEBUG] Fast-path check: primary={_fast_primary}, has_attachment={has_attachment}, has_memory_hint={_has_memory_hint}")
        if _fast_primary == Intent.CHAT and not has_attachment and not _has_memory_hint:
            print(f"[DEBUG] CHAT fast-path triggered!")
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
            full_response: list[str] = []
            async for chunk in self._adapter.chat(prompt_messages):
                full_response.append(chunk.text)
                # Filter at chunk level so leaks never reach the client
                safe_text = _filter_system_leaks(chunk.text)
                yield StreamChunk(text=safe_text, done=chunk.done)
            response_text = _filter_system_leaks("".join(full_response))
            self._store.append(session_id, Message(role=Role.USER, content=message))
            self._store.append(session_id, Message(role=Role.ASSISTANT, content=response_text))
            total_ms = round((time.monotonic() - t0) * 1000)
            self._metrics.record("chat", latency_ms=total_ms, success=True)
            _obs.emit("turn_complete", intent="chat", confidence=_fast_conf,
                      tokens_approx=_approx_tokens(response_text),
                      total_latency_ms=total_ms,
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

        # Single explicit resolution — no more mid-pipeline mutation
        effective_intent = _resolve_intent(primary_intent, top_tool, confidence)

        # Track intent history for this session (fed into AgentLoop for follow-ups)
        intent_history = self._update_intent_history(session_id, effective_intent)

        # ── 4. File attachment ─────────────────────────────────────────────
        file_attachment: Optional[FileAttachment] = None
        if file and filename:
            from tools.file_reader import parse_file
            file_attachment = await parse_file(
                data=file,
                filename=filename,
                content_type=content_type or "application/octet-stream",
                chunk_size=settings.localmind_chunk_size_tokens,
                original_path=original_path,
            )

        # ── 5. Memory retrieval ────────────────────────────────────────────
        # Fast-path: skip memory retrieval for short CHAT queries to avoid ChromaDB cold-start
        word_count = len(message.strip().split())
        if effective_intent == Intent.CHAT and word_count < 5:
            memory_facts = []
            _obs.emit("memory_retrieved", facts=0, latency_ms=0)
        else:
            # Memory retrieval
            t_mem = time.monotonic()
            memory_facts = await self._memory.compose(
                query=message,
                intent=effective_intent,
                session_id=session_id,
            )
            _obs.emit("memory_retrieved",
                      facts=len(memory_facts),
                      latency_ms=round((time.monotonic() - t_mem) * 1000))

        # ── 6. Tool dispatch ───────────────────────────────────────────
        tool_result: Optional[ToolResult] = None
        
        # CRITICAL: FILE_TASK and SHELL must always call real tools - never allow LLM fabrication
        # These intents are handled directly, NOT through agent loop
        if effective_intent in (Intent.FILE_TASK, Intent.SHELL):
            logger.info(f"[engine] Direct tool dispatch for {effective_intent.value} - preventing hallucination")
            t_tool = time.monotonic()
            try:
                tool_result = await dispatch(effective_intent, message)
                
                # Filter injection from tool output
                if tool_result and tool_result.content:
                    filtered_content = _filter_tool_injection(tool_result.content)
                    tool_result = ToolResult(
                        content=filtered_content,
                        risk=tool_result.risk,
                        source=tool_result.source,
                        metadata=tool_result.metadata if hasattr(tool_result, "metadata") else {},
                        requires_confirmation=tool_result.requires_confirmation,
                    )
                
                tool_latency = round((time.monotonic() - t_tool) * 1000)
                _obs.emit("tool_dispatched", tool=effective_intent.value, success=bool(tool_result), latency_ms=tool_latency)
                record_tool_outcome(self._store, effective_intent.value, success=bool(tool_result), latency_ms=tool_latency)
                self._metrics.record(effective_intent.value, latency_ms=tool_latency, success=bool(tool_result))
                
            except Exception as e:
                logger.error(f"Direct tool dispatch failed for {effective_intent.value}: {e}")
                _obs.emit("tool_failed", tool=effective_intent.value, error=str(e)[:80])
                tool_result = None
        
        # Agent intents (WEB_SEARCH, CODE_EXEC, FILE_WRITE, MEMORY_OP) get tool result in agent loop
        elif effective_intent in AGENT_INTENTS:
            # No initial dispatch - agent loop handles it
            pass
        
        # CHAT and other intents get no tool result
        else:
            tool_result = None

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

        active_adapter = self._adapter_for(effective_intent)
        prompt_messages = context_builder.build(ctx, active_adapter.context_window)
        prompt_messages = await maybe_compress_history(
            prompt_messages, active_adapter, active_adapter.context_window
        )

        # ── 7a/7b. Stream response ─────────────────────────────────────────
        full_response_chunks: list[str] = []

        if effective_intent in AGENT_INTENTS:
            _obs.emit("agent_loop_start", intent=effective_intent.value)
            async for chunk in self._agent_loop.run(
                messages=prompt_messages,
                intent=effective_intent,
                initial_tool_result=tool_result,
                available_tools=tools,
                confidence=confidence,
                adapter=active_adapter,
                intent_history=intent_history,
            ):
                safe_text = _filter_system_leaks(chunk.text)
                full_response_chunks.append(safe_text)
                yield StreamChunk(text=safe_text, done=chunk.done)
        else:
            async for chunk in active_adapter.chat(prompt_messages):
                safe_text = _filter_system_leaks(chunk.text)
                full_response_chunks.append(safe_text)
                yield StreamChunk(text=safe_text, done=chunk.done)

        response_text = "".join(full_response_chunks)

        # ── 8. Secondary intent ────────────────────────────────────────────
        if secondary_intent:
            match secondary_intent:
                case Intent.FILE_WRITE if tool_result:
                    try:
                        from tools.file_writer import write_response
                        await write_response(message=message, content=response_text)
                    except Exception as e:
                        logger.warning(f"Secondary FILE_WRITE failed: {e}")
                case Intent.MEMORY_OP:
                    # Secondary memory op: treat the full user message as a fact candidate
                    await self._extract_and_store_facts(message, session_id, _obs)
                case _:
                    logger.debug(
                        f"[engine] secondary intent {secondary_intent.value} — no handler, skipping"
                    )

        # ── 9. Memory persistence ──────────────────────────────────────────
        if effective_intent == Intent.MEMORY_OP:
            await self._extract_and_store_facts(message, session_id, _obs)

        # Post-response memory: assistant's own answer can surface new facts
        await self._maybe_store_response_facts(response_text, session_id, _obs)

        # ── 10. Persist history ────────────────────────────────────────────
        self._store.append(session_id, Message(role=Role.USER, content=message))
        self._store.append(session_id, Message(role=Role.ASSISTANT, content=response_text))

        total_ms = round((time.monotonic() - t0) * 1000)
        self._metrics.record(effective_intent.value, latency_ms=total_ms, success=True)
        _obs.emit("turn_complete",
                  intent=effective_intent.value,
                  confidence=round(confidence, 2),
                  tokens_approx=_approx_tokens(response_text),
                  total_latency_ms=total_ms,
                  memory_facts=len(memory_facts),
                  agent_mode=effective_intent in AGENT_INTENTS)

    # ── Memory helpers ─────────────────────────────────────────────────────

    async def _extract_and_store_facts(
        self, message: str, session_id: str, obs: ObsCollector
    ) -> None:
        import re
        fact = re.sub(
            r"^\s*(remember|note|store|keep in mind)\s*(that\s*)?",
            "", message, flags=re.IGNORECASE,
        ).strip()
        if not fact:
            return
        await self._store_fact_if_valid(fact, session_id, obs, source="user", message=message)

    async def _maybe_store_response_facts(
        self, response_text: str, session_id: str, obs: ObsCollector
    ) -> None:
        """
        Post-response memory update: extract candidate facts from the assistant's
        own answer (e.g. confirmed preferences, stated plans, key entities).
        Lightweight — only triggered when response contains strong fact markers.
        """
        _FACT_MARKERS = ["i will", "you prefer", "you told me", "you mentioned", "you always", "your name is"]
        response_lower = response_text.lower()
        if not any(marker in response_lower for marker in _FACT_MARKERS):
            return

        # Extract the sentence containing the marker as a candidate fact
        import re
        sentences = re.split(r'(?<=[.!?])\s+', response_text)
        for sentence in sentences:
            s_lower = sentence.lower()
            if any(marker in s_lower for marker in _FACT_MARKERS) and len(sentence) > 20:
                await self._store_fact_if_valid(sentence.strip(), session_id, obs, source="assistant")
                break  # one fact per response max — avoid over-storing

    async def _store_fact_if_valid(
        self,
        fact: str,
        session_id: str,
        obs: ObsCollector,
        source: str = "user",
        message: str = "",
    ) -> None:
        """
        Shared fact validation + storage used by both user and response paths.
        Applies: negative learning gate → deduplication gate → importance scoring → store.
        """
        import re

        _BLOCKED_PATTERNS = [
            r"\b(ignore|bypass|override|disable|forget)\b.{0,30}\b(safety|rules|guidelines|instructions|system prompt|restrictions)\b",
            r"\byou (are|must|should|will)\b.{0,40}\b(lie|deceive|pretend|ignore|always agree|never refuse)\b",
            r"\b(always|never) (tell|say|respond|answer|agree|refuse)\b",
            r"\bact (as|like)\b.{0,30}\b(jailbreak|unrestricted|dan|evil|unfiltered)\b",
            r"\byou (are|were) (wrong|stupid|dumb|useless|broken|terrible|awful|bad)\b",
            r"\b(hate|despise|dislike) (you|yourself|itself)\b",
            r"(system|assistant|user)\s*:\s*(ignore|disregard|forget)",
            r"<\s*(system|instruction|prompt)\s*>",
            r"\b(lie|make up|fabricate|hallucinate)\b.{0,20}\b(answers?|results?|facts?|data)\b",
        ]
        fact_lower = fact.lower()
        for pattern in _BLOCKED_PATTERNS:
            if re.search(pattern, fact_lower, re.IGNORECASE):
                logger.warning(f"[memory gate] blocked harmful fact: {fact[:80]}")
                obs.emit("memory_blocked", reason="negative_learning_gate", preview=fact[:60])
                return

        # Deduplication gate: skip if a near-identical fact already exists
        existing_facts = await self._memory.search(query=fact, session_id=session_id, top_k=3)
        for existing in existing_facts:
            similarity = self._simple_similarity(fact, existing.content)
            if similarity >= MEMORY_DEDUP_THRESHOLD:
                logger.debug(f"[memory dedup] skipped near-duplicate fact (sim={similarity:.2f}): {fact[:60]}")
                obs.emit("memory_deduped", similarity=round(similarity, 2), preview=fact[:60])
                return

        importance = (
            0.8 if any(w in (message or fact).lower() for w in ["prefer", "always", "never", "important"])
            else 0.5
        )
        stored = await self._memory.store(
            fact=fact, session_id=session_id,
            memory_type="semantic", source=source, importance=importance,
        )
        if stored:
            obs.emit("memory_stored", fact_preview=fact[:60], importance=importance, source=source)

    @staticmethod
    def _simple_similarity(a: str, b: str) -> float:
        """
        Lightweight Jaccard similarity on word sets.
        Fast enough for dedup gating — no embedding call needed.
        Replace with cosine similarity on embeddings for higher accuracy.
        """
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)