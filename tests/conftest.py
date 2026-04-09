"""
Shared pytest fixtures for all LocalMind tests.
"""
from __future__ import annotations
import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Ensure tests never hit a real Ollama instance or write to production DB."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:19999")  # nothing listens here
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("LOCALMIND_CODE_EXEC_ENABLED", "true")
    monkeypatch.setenv("LOCALMIND_LOG_LEVEL", "WARNING")


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary SQLite DB that is cleaned up after the test."""
    return str(tmp_path / "test.db")


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary SQLite database file."""
    return str(tmp_path / "test.db")


@pytest.fixture
def sample_pdf_bytes():
    """Minimal valid PDF bytes for testing the file reader."""
    # Smallest valid PDF structure
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        b"/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 44>>stream\n"
        b"BT /F1 12 Tf 100 700 Td (Hello LocalMind) Tj ET\n"
        b"endstream\nendobj\n"
        b"xref\n0 5\n"
        b"0000000000 65535 f \n"
        b"trailer<</Size 5/Root 1 0 R>>\n"
        b"startxref\n9\n%%EOF"
    )


@pytest.fixture
def sample_txt_bytes():
    return b"This is a test document.\n\nIt has two paragraphs.\n\nAnd a third one."
