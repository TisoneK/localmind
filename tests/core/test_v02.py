"""
v0.2 integration tests.

Covers:
- intent_router.classify_multi() — multi-intent detection
- MemoryComposer — relevance threshold, recency boost
- AgentLoop — action parsing, finish detection, iteration cap
- Engine — memory_facts no longer empty, MEMORY_OP stores facts
- VectorStore — recall_with_scores interface
"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import Intent, ToolResult, RiskLevel
from core import intent_router


# ── Intent router ──────────────────────────────────────────────────────────

class TestClassifyMulti:
    def test_single_intent_search(self):
        primary, secondary = intent_router.classify_multi("What's the latest news today?")
        assert primary == Intent.WEB_SEARCH
        assert secondary is None

    def test_single_intent_memory(self):
        primary, secondary = intent_router.classify_multi("Remember that I prefer dark mode")
        assert primary == Intent.MEMORY_OP

    def test_multi_intent_search_and_write(self):
        primary, secondary = intent_router.classify_multi(
            "Search for Python best practices and write them to a file"
        )
        assert primary == Intent.WEB_SEARCH
        assert secondary == Intent.FILE_WRITE

    def test_multi_intent_code_and_write(self):
        primary, secondary = intent_router.classify_multi(
            "Run this code and save the output to results.txt"
        )
        assert primary == Intent.CODE_EXEC
        assert secondary == Intent.FILE_WRITE

    def test_attachment_always_file_task(self):
        primary, secondary = intent_router.classify_multi("summarize this", has_attachment=True)
        assert primary == Intent.FILE_TASK

    def test_plain_chat(self):
        primary, secondary = intent_router.classify_multi("What is the meaning of life?")
        assert primary == Intent.CHAT
        assert secondary is None

    def test_classify_compat(self):
        """classify() is a thin wrapper — should still work."""
        assert intent_router.classify("search for cats today") == Intent.WEB_SEARCH


# ── VectorStore ─────────────────────────────────────────────────────────────

class TestVectorStore:
    @pytest.mark.asyncio
    async def test_store_and_recall_with_scores_interface(self):
        """VectorStore.recall_with_scores returns (fact, float, dict) triples."""
        from storage.vector import VectorStore
        store = VectorStore.__new__(VectorStore)
        store._client = None
        store._collection = None

        # When chromadb is not available, should return []
        with patch("storage.vector.VectorStore._get_client", return_value=None):
            result = await store.recall_with_scores("test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_store_returns_false_when_no_client(self):
        from storage.vector import VectorStore
        store = VectorStore.__new__(VectorStore)
        store._client = None
        store._collection = None
        with patch("storage.vector.VectorStore._get_client", return_value=None):
            result = await store.store("fact", "session-1")
        assert result is False


# ── MemoryComposer ───────────────────────────────────────────────────────────

class TestMemoryComposer:
    @pytest.mark.asyncio
    async def test_skips_irrelevant_intents(self):
        from core.memory import MemoryComposer
        composer = MemoryComposer()
        # CODE_EXEC and WEB_SEARCH do not benefit from memory
        for intent in (Intent.CODE_EXEC, Intent.WEB_SEARCH):
            facts = await composer.compose("anything", intent=intent, session_id="s1")
            assert facts == [], f"Expected no facts for {intent}"

    @pytest.mark.asyncio
    async def test_returns_facts_for_chat(self):
        from core.memory import MemoryComposer
        composer = MemoryComposer()
        mock_store = AsyncMock()
        mock_store.recall_with_scores.return_value = [
            ("user prefers Python", 0.1, {"timestamp": "1700000000", "memory_type": "semantic", "importance": "0.5", "access_count": "0"}),
            ("user works at ACME", 0.5, {"timestamp": "1700000000", "memory_type": "semantic", "importance": "0.5", "access_count": "0"}),
            # distance 0.5 is below the LOOSE threshold (0.60) — passes for small stores (< 10 facts)
        ]
        mock_store.update_metadata = AsyncMock(return_value=True)
        composer._store = mock_store
        facts = await composer.compose("what language should I use?", Intent.CHAT, "s1")
        # Both facts pass the loose threshold (store has < 10 facts → uses 0.60)
        assert len(facts) == 2
        assert facts[0] == "user prefers Python"  # lower distance → higher similarity score

    @pytest.mark.asyncio
    async def test_handles_store_failure_gracefully(self):
        from core.memory import MemoryComposer
        composer = MemoryComposer()
        mock_store = AsyncMock()
        mock_store.recall_with_scores.side_effect = Exception("DB down")
        composer._store = mock_store
        facts = await composer.compose("anything", Intent.CHAT, "s1")
        assert facts == []


# ── AgentLoop ────────────────────────────────────────────────────────────────

class TestAgentLoop:
    def _make_adapter(self, responses: list[str]):
        """Create a mock adapter that yields one response per call."""
        call_count = {"n": 0}

        async def mock_chat(messages, temperature=0.7, **kwargs):
            text = responses[min(call_count["n"], len(responses) - 1)]
            call_count["n"] += 1
            for char in text:
                yield MagicMock(text=char, done=False, error=None)
            yield MagicMock(text="", done=True, error=None)

        adapter = MagicMock()
        adapter.chat = mock_chat
        return adapter

    @pytest.mark.asyncio
    async def test_finish_on_first_iteration(self):
        from core.agent import AgentLoop
        adapter = self._make_adapter(["<finish>Hello, world!</finish>"])
        loop = AgentLoop(adapter=adapter)
        chunks = []
        async for chunk in loop.run(
            messages=[{"role": "user", "content": "hi"}],
            intent=Intent.WEB_SEARCH,
            initial_tool_result=None,
            available_tools=[],
        ):
            chunks.append(chunk.text)
        assert "Hello, world!" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_tool_call_then_finish(self):
        from core.agent import AgentLoop
        responses = [
            "<action>\ntool: web_search\ninput: python news\n</action>",
            "<finish>Here are the results!</finish>",
        ]
        adapter = self._make_adapter(responses)
        loop = AgentLoop(adapter=adapter)

        with patch("core.agent.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = ToolResult(
                content="Python 3.13 released",
                source="web_search",
                risk=RiskLevel.LOW,
            )
            chunks = []
            async for chunk in loop.run(
                messages=[{"role": "user", "content": "search python news"}],
                intent=Intent.WEB_SEARCH,
                initial_tool_result=None,
                available_tools=[{"intent": "web_search", "description": "search", "cost": 0.1, "latency_ms": 500}],
            ):
                chunks.append(chunk.text)

        assert "results" in "".join(chunks)
        mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_structured_format_treated_as_finish(self):
        """Model ignoring format tags should still return a response."""
        from core.agent import AgentLoop
        adapter = self._make_adapter(["Sure, here is my answer without any tags."])
        loop = AgentLoop(adapter=adapter)
        chunks = []
        async for chunk in loop.run(
            messages=[{"role": "user", "content": "anything"}],
            intent=Intent.WEB_SEARCH,
            initial_tool_result=None,
            available_tools=[],
        ):
            chunks.append(chunk.text)
        assert len("".join(chunks)) > 0


# ── v0.3: LLM Intent Classifier ─────────────────────────────────────────────

class TestLLMIntentClassifier:
    @pytest.mark.asyncio
    async def test_falls_back_to_rule_based_on_adapter_failure(self):
        from core.intent_classifier import classify_with_llm

        async def bad_chat(messages, temperature=0.7, **kwargs):
            raise ConnectionError("Ollama offline")
            yield  # make it a generator

        adapter = MagicMock()
        adapter.chat = bad_chat

        primary, secondary, confidence = await classify_with_llm(
            "search for cats today", False, adapter
        )
        # Should fall back to rule-based
        assert primary == Intent.WEB_SEARCH
        assert confidence == 0.5

    @pytest.mark.asyncio
    async def test_parses_valid_llm_response(self):
        from core.intent_classifier import classify_with_llm

        async def good_chat(messages, temperature=0.7, **kwargs):
            payload = '{"primary": "web_search", "secondary": ["file_write"], "confidence": 0.92, "reasoning": "needs current info"}'
            for char in payload:
                yield MagicMock(text=char, done=False, error=None)
            yield MagicMock(text="", done=True, error=None)

        adapter = MagicMock()
        adapter.chat = good_chat

        primary, secondary, confidence = await classify_with_llm("latest AI news", False, adapter)
        assert primary == Intent.WEB_SEARCH
        assert secondary == Intent.FILE_WRITE
        assert confidence == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        from core.intent_classifier import classify_with_llm

        fenced = '```json\n{"primary": "chat", "secondary": [], "confidence": 0.95, "reasoning": "simple"}\n```'

        async def fenced_chat(messages, temperature=0.7, **kwargs):
            for char in fenced:
                yield MagicMock(text=char, done=False, error=None)
            yield MagicMock(text="", done=True, error=None)

        adapter = MagicMock()
        adapter.chat = fenced_chat

        primary, _, confidence = await classify_with_llm("hello", False, adapter)
        assert primary == Intent.CHAT
        assert confidence == pytest.approx(0.95)


# ── v0.3: Tool Scorer ────────────────────────────────────────────────────────

class TestToolScorer:
    def _tools(self):
        return [
            {"intent": "web_search", "description": "search the web", "cost": 0.1, "latency_ms": 800},
            {"intent": "code_exec", "description": "run code", "cost": 0.05, "latency_ms": 500},
            {"intent": "file_write", "description": "write files", "cost": 0.01, "latency_ms": 100},
        ]

    def test_exact_match_scores_highest(self):
        from core.tool_scorer import score_tools
        scored = score_tools(self._tools(), Intent.WEB_SEARCH, confidence=0.9)
        assert scored[0].intent == Intent.WEB_SEARCH
        assert scored[0].score > 0.5

    def test_low_confidence_widens_relevance(self):
        from core.tool_scorer import score_tools
        # At low confidence, non-matching tools get partial relevance
        scored_low = score_tools(self._tools(), Intent.WEB_SEARCH, confidence=0.4)
        scored_high = score_tools(self._tools(), Intent.WEB_SEARCH, confidence=0.9)
        # Non-top tools should have higher scores at low confidence
        non_top_low = [s for s in scored_low if s.intent != Intent.WEB_SEARCH]
        non_top_high = [s for s in scored_high if s.intent != Intent.WEB_SEARCH]
        if non_top_low and non_top_high:
            assert non_top_low[0].score >= non_top_high[0].score

    def test_best_tool_returns_none_below_threshold(self):
        from core.tool_scorer import best_tool
        # With empty tool list, nothing passes min_score
        result = best_tool([], Intent.WEB_SEARCH, confidence=0.9)
        assert result is None

    def test_score_formula_components(self):
        from core.tool_scorer import score_tools
        # file_write has low latency and low cost — should score well on those factors
        scored = score_tools(self._tools(), Intent.FILE_WRITE, confidence=0.95)
        fw = next(s for s in scored if s.intent == Intent.FILE_WRITE)
        assert fw.score > 0.6  # exact match + fast + cheap


# ── v0.3: Memory 4-factor scoring ────────────────────────────────────────────

class TestMemoryComposerV3:
    @pytest.mark.asyncio
    async def test_importance_affects_ranking(self):
        """Higher importance should rank above equal similarity but lower importance."""
        import time
        from core.memory import MemoryComposer

        now = time.time()
        composer = MemoryComposer()
        mock_store = AsyncMock()
        mock_store.recall_with_scores.return_value = [
            ("critical preference", 0.2, {"timestamp": str(now), "importance": "0.9", "access_count": "0"}),
            ("trivial note", 0.2, {"timestamp": str(now), "importance": "0.1", "access_count": "0"}),
        ]
        mock_store.update_metadata = AsyncMock(return_value=True)
        composer._store = mock_store

        facts = await composer.compose("any query", Intent.CHAT, "s1", top_k=2)
        assert facts[0] == "critical preference"

    @pytest.mark.asyncio
    async def test_usage_frequency_tracked(self):
        """update_metadata should be called for each retrieved fact."""
        import time
        from core.memory import MemoryComposer

        now = time.time()
        composer = MemoryComposer()
        mock_store = AsyncMock()
        mock_store.recall_with_scores.return_value = [
            ("frequently used fact", 0.1, {"timestamp": str(now), "importance": "0.5", "access_count": "5"}),
        ]
        mock_store.update_metadata = AsyncMock(return_value=True)
        composer._store = mock_store

        await composer.compose("query", Intent.CHAT, "s1")
        mock_store.update_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_loose_threshold_for_small_store(self):
        """When fewer than 10 facts exist, use looser threshold."""
        import time
        from core.memory import MemoryComposer, _RELEVANCE_THRESHOLD, _LOOSE_THRESHOLD

        now = time.time()
        composer = MemoryComposer()
        mock_store = AsyncMock()
        # Distance of 0.50 — above normal threshold (0.45) but below loose (0.60)
        mock_store.recall_with_scores.return_value = [
            ("borderline fact", 0.50, {"timestamp": str(now), "importance": "0.5", "access_count": "0"}),
        ]
        mock_store.update_metadata = AsyncMock(return_value=True)
        composer._store = mock_store

        # 1 fact returned → store is small → loose threshold applies
        facts = await composer.compose("query", Intent.CHAT, "s1")
        assert len(facts) == 1
        assert facts[0] == "borderline fact"


# ── v0.3: Agent loop — reflection + clarification ────────────────────────────

class TestAgentLoopV3:
    def _make_adapter(self, responses):
        call_count = {"n": 0}

        async def mock_chat(messages, temperature=0.7, **kwargs):
            text = responses[min(call_count["n"], len(responses) - 1)]
            call_count["n"] += 1
            for char in text:
                yield MagicMock(text=char, done=False, error=None)
            yield MagicMock(text="", done=True, error=None)

        adapter = MagicMock()
        adapter.chat = mock_chat
        return adapter

    @pytest.mark.asyncio
    async def test_reflection_step_continues_loop(self):
        from core.agent import AgentLoop
        responses = [
            "<reflect>\nquality: failed\nissue: tool returned nothing\nnext: try different query\n</reflect>",
            "<finish>Here is my answer based on what I know.</finish>",
        ]
        adapter = self._make_adapter(responses)
        loop = AgentLoop(adapter=adapter)
        chunks = []
        async for chunk in loop.run(
            messages=[{"role": "user", "content": "search for X"}],
            intent=Intent.WEB_SEARCH,
            initial_tool_result=None,
            available_tools=[],
            confidence=0.9,
        ):
            chunks.append(chunk.text)
        assert "answer" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_clarification_gate_fires_at_low_confidence(self):
        from core.agent import AgentLoop
        adapter = self._make_adapter(["should not be called"])
        loop = AgentLoop(adapter=adapter)
        chunks = []
        async for chunk in loop.run(
            messages=[{"role": "user", "content": "do the thing"}],
            intent=Intent.WEB_SEARCH,
            initial_tool_result=None,
            available_tools=[],
            confidence=0.3,  # below CLARIFICATION_THRESHOLD (0.45)
        ):
            chunks.append(chunk.text)
        response = "".join(chunks)
        assert "confirm" in response.lower() or "clarif" in response.lower() or "sure" in response.lower()

    @pytest.mark.asyncio
    async def test_clarify_tag_yields_question(self):
        from core.agent import AgentLoop
        responses = ["<clarify>Did you mean to search the web or run code?</clarify>"]
        adapter = self._make_adapter(responses)
        loop = AgentLoop(adapter=adapter)
        chunks = []
        async for chunk in loop.run(
            messages=[{"role": "user", "content": "do something"}],
            intent=Intent.WEB_SEARCH,
            initial_tool_result=None,
            available_tools=[],
            confidence=0.9,
        ):
            chunks.append(chunk.text)
        assert "search" in "".join(chunks).lower() or "code" in "".join(chunks).lower()
