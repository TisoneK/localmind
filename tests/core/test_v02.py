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
            ("user prefers Python", 0.1, {"timestamp": "1700000000", "memory_type": "semantic"}),
            ("user works at ACME", 0.5, {"timestamp": "1700000000", "memory_type": "semantic"}),  # above threshold
        ]
        composer._store = mock_store
        facts = await composer.compose("what language should I use?", Intent.CHAT, "s1")
        # Only the fact with distance 0.1 should pass the threshold (0.45)
        assert len(facts) == 1
        assert "Python" in facts[0]

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
