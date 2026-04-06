"""
File Reader Tool — parses uploaded files and returns chunked text.

Supported formats:
- PDF (via PyMuPDF)
- DOCX (via python-docx)
- TXT, MD, CSV, JSON, and all plain-text code files
- XLSX / XLS (via pandas)

Chunking strategy:
- Split text into chunks of ~chunk_size tokens with overlap
- Overlap prevents answers being cut across chunk boundaries
- Chunks are stored on the FileAttachment and injected by the context builder
"""
from __future__ import annotations
import io
import logging
import re
from pathlib import Path

import tiktoken

from core.models import FileAttachment, RiskLevel
from core.config import settings
from tools.base import ToolResult

logger = logging.getLogger(__name__)
_ENCODER = tiktoken.get_encoding("cl100k_base")

# File extensions treated as plain text
PLAIN_TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".csv", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
    ".sh", ".bash", ".zsh", ".ps1", ".env", ".toml", ".ini",
    ".xml", ".sql", ".r", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".cs", ".php", ".rb", ".swift", ".kt",
}


def _count_tokens(text: str) -> int:
    return len(_ENCODER.encode(text))


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks by token count."""
    if not text.strip():
        return []

    # Split into sentences/paragraphs first for cleaner chunks
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_tokens = _count_tokens(para)

        if current_tokens + para_tokens > chunk_size and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            # Keep overlap: retain last paragraph(s) up to overlap tokens
            overlap_chunk: list[str] = []
            overlap_tokens = 0
            for p in reversed(current_chunk):
                pt = _count_tokens(p)
                if overlap_tokens + pt > overlap:
                    break
                overlap_chunk.insert(0, p)
                overlap_tokens += pt
            current_chunk = overlap_chunk
            current_tokens = overlap_tokens

        current_chunk.append(para)
        current_tokens += para_tokens

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def _extract_pdf(data: bytes) -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(stream=data, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    return "\n\n".join(pages)


def _extract_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_xlsx(data: bytes) -> str:
    import pandas as pd
    df = pd.read_excel(io.BytesIO(data))
    return df.to_string(index=False)


def _extract_csv(data: bytes) -> str:
    import pandas as pd
    df = pd.read_csv(io.BytesIO(data))
    return df.to_string(index=False)


def _extract_plain(data: bytes, encoding: str = "utf-8") -> str:
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _extract_text(data: bytes, filename: str, content_type: str) -> str:
    """Dispatch to the correct extractor based on file type."""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf" or content_type == "application/pdf":
        return _extract_pdf(data)

    if suffix == ".docx" or content_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return _extract_docx(data)

    if suffix in (".xlsx", ".xls"):
        return _extract_xlsx(data)

    if suffix == ".csv" or content_type == "text/csv":
        return _extract_csv(data)

    if suffix in PLAIN_TEXT_EXTENSIONS or content_type.startswith("text/"):
        return _extract_plain(data)

    # Unknown type — try plain text, log a warning
    logger.warning(f"Unknown file type: {filename} ({content_type}). Attempting plain text extraction.")
    return _extract_plain(data)


async def parse_file(
    data: bytes,
    filename: str,
    content_type: str,
    chunk_size: int = None,
) -> FileAttachment:
    """
    Parse a file and return a FileAttachment with text chunks.

    Args:
        data: Raw file bytes.
        filename: Original filename (used for extension detection).
        content_type: MIME type.
        chunk_size: Target chunk size in tokens (default: from settings).

    Returns:
        FileAttachment with populated chunks list.
    """
    chunk_size = chunk_size or settings.localmind_chunk_size_tokens
    overlap = settings.localmind_chunk_overlap_tokens
    max_bytes = settings.localmind_max_file_size_mb * 1024 * 1024

    if len(data) > max_bytes:
        raise ValueError(
            f"File too large: {len(data) / 1024 / 1024:.1f} MB. "
            f"Maximum is {settings.localmind_max_file_size_mb} MB."
        )

    logger.info(f"Parsing file: {filename} ({len(data)} bytes, {content_type})")

    text = _extract_text(data, filename, content_type)
    chunks = _chunk_text(text, chunk_size, overlap)

    logger.info(f"Extracted {len(text)} chars → {len(chunks)} chunks from {filename}")

    return FileAttachment(
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        chunks=chunks,
    )


async def run(input_data: dict, context: dict) -> ToolResult:
    """
    Tool entry point (for registry dispatch).
    File reading is handled directly in the engine via parse_file().
    This entry point is for explicit file-read requests without attachment.
    """
    return ToolResult(
        content="Please attach a file using the file upload button to use the file reader.",
        risk=RiskLevel.LOW,
        source="file_reader",
    )
