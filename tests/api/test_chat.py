"""
Integration tests for the /api/chat endpoint.
The engine is mocked — no Ollama required.
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
from api.app import create_app
from core.models import StreamChunk


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def _mock_engine_stream(*chunks: str):
    """Return an async generator that yields StreamChunks."""
    async def _gen(*args, **kwargs):
        for text in chunks:
            yield StreamChunk(text=text, done=False)
        yield StreamChunk(text="", done=True)
    return _gen


@pytest.mark.asyncio
async def test_chat_returns_sse_stream(client):
    mock_process = _mock_engine_stream("Hello", " world")

    with patch("api.routes.chat._engine") as mock_engine:
        mock_engine.process = mock_process
        response = client.post(
            "/api/chat",
            data={"message": "Hi", "session_id": "test-session"},
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_chat_missing_message_returns_422(client):
    response = client.post("/api/chat", data={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_file_too_large_returns_413(client):
    big_file = b"x" * (51 * 1024 * 1024)  # 51 MB

    with patch("api.routes.chat._engine"):
        response = client.post(
            "/api/chat",
            data={"message": "summarise"},
            files={"file": ("big.pdf", big_file, "application/pdf")},
        )

    assert response.status_code == 413


def test_health_endpoint_structure(client):
    with patch("api.routes.health.get_adapter") as mock_get:
        mock_adapter = AsyncMock()
        mock_adapter.health_check = AsyncMock(return_value=True)
        mock_get.return_value = mock_adapter

        response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "ollama_reachable" in data
    assert "tools" in data


def test_sessions_list_returns_list(client):
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
