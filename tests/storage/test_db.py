"""Tests for the SQLite session store."""
import pytest
import time
from storage.db import SessionStore
from core.models import Message, Role


@pytest.fixture
def store(tmp_db):
    return SessionStore(tmp_db)


def test_ensure_session_creates(store):
    store.ensure_session("s1")
    sessions = store.list_sessions()
    assert any(s["id"] == "s1" for s in sessions)


def test_ensure_session_idempotent(store):
    store.ensure_session("s1")
    store.ensure_session("s1")  # should not raise
    sessions = store.list_sessions()
    assert sum(1 for s in sessions if s["id"] == "s1") == 1


def test_append_and_retrieve(store):
    msg = Message(role=Role.USER, content="Hello LocalMind", timestamp=time.time())
    store.append("s1", msg)
    history = store.get_history("s1")
    assert len(history) == 1
    assert history[0].content == "Hello LocalMind"
    assert history[0].role == Role.USER


def test_history_order(store):
    store.append("s1", Message(role=Role.USER, content="First", timestamp=1.0))
    store.append("s1", Message(role=Role.ASSISTANT, content="Second", timestamp=2.0))
    store.append("s1", Message(role=Role.USER, content="Third", timestamp=3.0))
    history = store.get_history("s1")
    assert [m.content for m in history] == ["First", "Second", "Third"]


def test_multiple_sessions_isolated(store):
    store.append("s1", Message(role=Role.USER, content="Session 1 message"))
    store.append("s2", Message(role=Role.USER, content="Session 2 message"))
    assert len(store.get_history("s1")) == 1
    assert len(store.get_history("s2")) == 1
    assert store.get_history("s1")[0].content == "Session 1 message"


def test_delete_session(store):
    store.append("s1", Message(role=Role.USER, content="Hello"))
    deleted = store.delete_session("s1")
    assert deleted is True
    assert store.get_history("s1") == []


def test_delete_nonexistent_session(store):
    deleted = store.delete_session("nonexistent")
    assert deleted is False


def test_list_sessions_empty(store):
    assert store.list_sessions() == []


def test_list_sessions_with_data(store):
    store.append("s1", Message(role=Role.USER, content="Hi"))
    store.append("s1", Message(role=Role.ASSISTANT, content="Hello"))
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["message_count"] == 2


def test_clear_all(store):
    store.append("s1", Message(role=Role.USER, content="A"))
    store.append("s2", Message(role=Role.USER, content="B"))
    store.clear_all()
    assert store.list_sessions() == []
