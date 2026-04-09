"""
API integration tests — v0.3

Covers: chat SSE streaming, sessions CRUD, health endpoint, tool registry.
Engine is mocked so tests run without Ollama.
"""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.app import create_app
    return TestClient(create_app(), raise_server_exceptions=False)


def _mock_stream(text="Hello!", intent="chat", confidence=0.95):
    """Patch Engine.process to yield predictable chunks + obs events."""
    from core.models import StreamChunk

    async def _process(self, message, session_id, file=None, filename=None,
                        content_type=None, obs=None):
        if obs:
            obs.emit("intent_classified", primary=intent, secondary="none",
                     confidence=confidence)
            obs.emit("memory_retrieved", facts=0, latency_ms=5)
            obs.emit("turn_complete", intent=intent, confidence=confidence,
                     tokens_approx=len(text) // 4, total_latency_ms=50,
                     memory_facts=0, agent_mode=False)
        for char in text:
            yield StreamChunk(text=char, done=False)
        yield StreamChunk(text="", done=True)

    return patch("core.engine.Engine.process", _process)


def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_returns_200(self, client):
        with patch("adapters.ollama.OllamaAdapter.health_check",
                   new_callable=AsyncMock, return_value=True):
            assert client.get("/api/health").status_code == 200

    def test_schema_has_required_fields(self, client):
        with patch("adapters.ollama.OllamaAdapter.health_check",
                   new_callable=AsyncMock, return_value=True):
            data = client.get("/api/health").json()
        for field in ("status", "ollama_reachable", "model", "tools", "version"):
            assert field in data, f"missing field: {field}"

    def test_degraded_when_ollama_down(self, client):
        with patch("adapters.ollama.OllamaAdapter.health_check",
                   new_callable=AsyncMock, return_value=False):
            data = client.get("/api/health").json()
        assert data["status"] == "degraded"
        assert data["ollama_reachable"] is False

    def test_lists_all_registered_tools(self, client):
        with patch("adapters.ollama.OllamaAdapter.health_check",
                   new_callable=AsyncMock, return_value=True):
            tools = client.get("/api/health").json()["tools"]
        intents = {t["intent"] for t in tools}
        for expected in ("web_search", "code_exec", "memory_op", "file_task"):
            assert expected in intents


# ── Chat ──────────────────────────────────────────────────────────────────────

class TestChat:
    def test_returns_200(self, client):
        with _mock_stream():
            r = client.post("/api/chat", data={"message": "hi", "session_id": "t1"})
        assert r.status_code == 200

    def test_content_type_is_sse(self, client):
        with _mock_stream():
            r = client.post("/api/chat", data={"message": "hi", "session_id": "t2"})
        assert "text/event-stream" in r.headers.get("content-type", "")

    def test_session_id_in_response_header(self, client):
        with _mock_stream():
            r = client.post("/api/chat", data={"message": "hi", "session_id": "my-sid"})
        assert r.headers.get("x-session-id") == "my-sid"

    def test_autogenerates_session_id_if_omitted(self, client):
        with _mock_stream():
            r = client.post("/api/chat", data={"message": "hi"})
        assert len(r.headers.get("x-session-id", "")) > 0

    def test_streams_text_content(self, client):
        with _mock_stream("Hello world"):
            r = client.post("/api/chat", data={"message": "hi", "session_id": "t3"})
        events = _parse_sse(r.text)
        full = "".join(e.get("text", "") for e in events if "text" in e)
        assert "Hello world" in full

    def test_emits_done_true(self, client):
        with _mock_stream():
            r = client.post("/api/chat", data={"message": "hi", "session_id": "t4"})
        events = _parse_sse(r.text)
        assert any(e.get("done") is True for e in events)

    def test_emits_intent_classified_obs_event(self, client):
        with _mock_stream(intent="web_search"):
            r = client.post("/api/chat", data={"message": "search", "session_id": "t5"})
        events = _parse_sse(r.text)
        obs = [e["obs_event"] for e in events if "obs_event" in e]
        intent_events = [o for o in obs if o["type"] == "intent_classified"]
        assert len(intent_events) >= 1
        assert intent_events[0]["data"]["primary"] == "web_search"

    def test_emits_intent_convenience_payload(self, client):
        with _mock_stream(intent="memory_op", confidence=0.82):
            r = client.post("/api/chat", data={"message": "remember", "session_id": "t6"})
        events = _parse_sse(r.text)
        intent_payloads = [e for e in events if "intent" in e and "obs_event" not in e]
        assert len(intent_payloads) >= 1
        assert intent_payloads[0]["intent"] == "memory_op"
        assert abs(intent_payloads[0]["confidence"] - 0.82) < 0.01

    def test_emits_turn_complete_obs_event(self, client):
        with _mock_stream():
            r = client.post("/api/chat", data={"message": "hi", "session_id": "t7"})
        events = _parse_sse(r.text)
        obs = [e["obs_event"] for e in events if "obs_event" in e]
        complete_events = [o for o in obs if o["type"] == "turn_complete"]
        assert len(complete_events) >= 1

    def test_file_too_large_returns_413(self, client):
        import io
        r = client.post(
            "/api/chat",
            data={"message": "read this", "session_id": "t8"},
            files={"file": ("big.txt", io.BytesIO(b"x" * (51 * 1024 * 1024)), "text/plain")},
        )
        assert r.status_code == 413

    def test_missing_message_returns_422(self, client):
        r = client.post("/api/chat", data={"session_id": "t9"})
        assert r.status_code == 422


# ── Sessions ──────────────────────────────────────────────────────────────────

class TestSessions:
    def _seed(self, client, sid):
        with _mock_stream("response"):
            client.post("/api/chat", data={"message": "hello", "session_id": sid})

    def test_list_returns_200(self, client):
        assert client.get("/api/sessions").status_code == 200
        assert isinstance(client.get("/api/sessions").json(), list)

    def test_history_404_for_unknown_session(self, client):
        assert client.get("/api/sessions/no-such-session/history").status_code == 404

    def test_delete_404_for_unknown_session(self, client):
        assert client.delete("/api/sessions/no-such-session").status_code == 404

    def test_delete_returns_deleted_id(self, client):
        sid = "to-delete-1"
        self._seed(client, sid)
        r = client.delete(f"/api/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["deleted"] == sid

    def test_history_has_messages_after_chat(self, client):
        sid = "history-check-1"
        self._seed(client, sid)
        msgs = client.get(f"/api/sessions/{sid}/history").json()
        assert len(msgs) >= 1
        assert any(m["role"] == "user" for m in msgs)


# ── Tool registry ─────────────────────────────────────────────────────────────

class TestToolRegistry:
    def test_all_built_in_tools_registered(self):
        from tools import available_tools
        intents = {t["intent"] for t in available_tools()}
        for i in ("web_search", "code_exec", "memory_op", "file_task"):
            assert i in intents

    def test_tool_metadata_schema(self):
        from tools import available_tools
        for t in available_tools():
            assert isinstance(t["description"], str) and t["description"]
            assert isinstance(t["cost"], float) and t["cost"] >= 0
            assert isinstance(t["latency_ms"], int) and t["latency_ms"] > 0

    @pytest.mark.asyncio
    async def test_dispatch_none_for_unregistered_chat(self):
        from tools import dispatch
        from core.models import Intent
        assert await dispatch(Intent.CHAT, "hello") is None

    @pytest.mark.asyncio
    async def test_web_search_tool(self):
        from tools.web_search import web_search
        with patch("tools.web_search._search_ddg", new_callable=AsyncMock) as m:
            m.return_value = [{"title": "Result", "href": "https://ex.com", "body": "snippet"}]
            result = await web_search("test")
        assert result.source == "web_search"
        assert "Result" in result.content

    @pytest.mark.asyncio
    async def test_code_exec_runs_python(self):
        from tools.code_exec import code_exec
        result = await code_exec("```python\nprint('test output')\n```")
        assert result.source == "code_exec"
        assert "test output" in result.content

    @pytest.mark.asyncio
    async def test_code_exec_no_code_returns_guidance(self):
        from tools.code_exec import code_exec
        result = await code_exec("what is 2 + 2")
        assert "No executable" in result.content

    @pytest.mark.asyncio
    async def test_memory_op_store(self):
        from tools.memory_tool import memory_op
        with patch("storage.vector.VectorStore.store", new_callable=AsyncMock, return_value=True):
            result = await memory_op("remember that I prefer Python")
        assert result.source == "memory"
        assert "Python" in result.content or "Remembered" in result.content

    @pytest.mark.asyncio
    async def test_memory_op_list(self):
        from tools.memory_tool import memory_op
        with patch("storage.vector.VectorStore.list_all_with_metadata", new_callable=AsyncMock,
                   return_value=[{"fact": "likes Python", "memory_type": "semantic",
                                  "importance": "0.8", "access_count": "1"}]):
            result = await memory_op("what do you know about me")
        assert "likes Python" in result.content

    @pytest.mark.asyncio
    async def test_file_task_no_attachment_returns_guidance(self):
        from tools.file_reader import file_task
        result = await file_task("read my doc")
        assert result.source == "file_reader"
        assert "attach" in result.content.lower() or "file" in result.content.lower()
