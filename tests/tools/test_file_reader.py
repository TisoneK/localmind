"""Tests for the file reader tool."""
import pytest
from tools.file_reader import _chunk_text, _extract_plain, parse_file


def test_chunk_text_basic():
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    chunks = _chunk_text(text, chunk_size=50, overlap=10)
    assert len(chunks) >= 1
    assert all(isinstance(c, str) for c in chunks)


def test_chunk_text_empty():
    assert _chunk_text("", chunk_size=100, overlap=20) == []


def test_chunk_text_single_paragraph():
    text = "Just one paragraph with some content here."
    chunks = _chunk_text(text, chunk_size=200, overlap=20)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_overlap():
    # With very small chunk size, we should get multiple chunks
    text = "\n\n".join([f"Paragraph {i} with some content." for i in range(10)])
    chunks = _chunk_text(text, chunk_size=30, overlap=10)
    assert len(chunks) > 1


def test_extract_plain_utf8():
    data = b"Hello, world!"
    result = _extract_plain(data)
    assert result == "Hello, world!"


def test_extract_plain_latin1_fallback():
    data = b"Caf\xe9"  # latin-1 encoded
    result = _extract_plain(data)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_parse_file_txt(sample_txt_bytes):
    attachment = await parse_file(
        data=sample_txt_bytes,
        filename="test.txt",
        content_type="text/plain",
        chunk_size=100,
    )
    assert attachment.filename == "test.txt"
    assert attachment.size_bytes == len(sample_txt_bytes)
    assert len(attachment.chunks) >= 1
    assert "test document" in " ".join(attachment.chunks)


@pytest.mark.asyncio
async def test_parse_file_too_large(monkeypatch):
    from core import config
    monkeypatch.setattr(config.settings, "localmind_max_file_size_mb", 0)
    with pytest.raises(ValueError, match="too large"):
        await parse_file(data=b"x" * 100, filename="big.txt", content_type="text/plain")


@pytest.mark.asyncio
async def test_parse_file_unknown_extension():
    # Should fall back to plain text extraction
    data = b"Some content in an unknown file type"
    attachment = await parse_file(data=data, filename="file.xyz", content_type="application/xyz")
    assert len(attachment.chunks) >= 1
