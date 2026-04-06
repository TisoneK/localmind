"""Tests for the context builder."""
import pytest
from core.context_builder import build, _count_tokens
from core.models import EngineContext, Intent, Message, Role, ToolResult, RiskLevel, FileAttachment


def _make_ctx(**kwargs) -> EngineContext:
    defaults = dict(
        session_id="test-session",
        message="Hello",
        intent=Intent.CHAT,
        history=[],
    )
    defaults.update(kwargs)
    return EngineContext(**defaults)


def test_build_returns_list_of_dicts():
    ctx = _make_ctx()
    messages = build(ctx)
    assert isinstance(messages, list)
    assert all(isinstance(m, dict) for m in messages)


def test_system_message_always_first():
    ctx = _make_ctx()
    messages = build(ctx)
    assert messages[0]["role"] == "system"


def test_user_message_always_last():
    ctx = _make_ctx(message="Tell me something")
    messages = build(ctx)
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "Tell me something"


def test_history_included():
    history = [
        Message(role=Role.USER, content="Hi"),
        Message(role=Role.ASSISTANT, content="Hello!"),
    ]
    ctx = _make_ctx(history=history, message="How are you?")
    messages = build(ctx)
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles


def test_tool_result_injected_in_system():
    tool_result = ToolResult(content="Search result: Paris", risk=RiskLevel.LOW, source="web_search")
    ctx = _make_ctx(tool_result=tool_result)
    messages = build(ctx)
    assert "Paris" in messages[0]["content"]


def test_file_attachment_chunks_injected():
    attachment = FileAttachment(
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=1000,
        chunks=["This is chunk one.", "This is chunk two."],
    )
    ctx = _make_ctx(file_attachment=attachment)
    messages = build(ctx)
    assert "chunk one" in messages[0]["content"]


def test_memory_facts_injected():
    ctx = _make_ctx(memory_facts=["User prefers Python", "User is based in Kenya"])
    messages = build(ctx)
    assert "Python" in messages[0]["content"]
    assert "Kenya" in messages[0]["content"]


def test_history_trimmed_on_budget():
    # Create a very small context window to force trimming
    long_history = [
        Message(role=Role.USER, content="A" * 500),
        Message(role=Role.ASSISTANT, content="B" * 500),
    ] * 10
    ctx = _make_ctx(history=long_history, message="New question")
    # 512 token window — most history should be trimmed
    messages = build(ctx, model_context_window=512)
    # Should still have system + at least the current user message
    assert messages[0]["role"] == "system"
    assert messages[-1]["content"] == "New question"


def test_count_tokens_basic():
    assert _count_tokens("hello world") > 0
    assert _count_tokens("") == 0
