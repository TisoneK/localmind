"""
File Reader tool — parses uploaded files into text chunks for the model.

Supported formats:
    PDF   → PyMuPDF (fitz)
    DOCX  → python-docx
    CSV   → pandas
    XLSX  → pandas + openpyxl
    TXT / MD / code files → plain UTF-8 read

Also exports parse_file() used directly by the engine for file attachments.
Registered as Intent.FILE_TASK in the tool registry.
"""
from __future__ import annotations
import logging
from pathlib import Path

from core.models import Intent, FileAttachment, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".py", ".js", ".ts",
                  ".json", ".yaml", ".yml", ".toml", ".html", ".xml", ".sh", ".rs", ".go"}


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping token-approximate chunks."""
    words = text.split()
    if not words:
        return []
    chunks = []
    i = 0
    while i < len(words):
        end = min(i + chunk_size, len(words))
        chunks.append(" ".join(words[i:end]))
        i += chunk_size - overlap
    return chunks


async def _parse_pdf(data: bytes) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        pages = [page.get_text() for page in doc]
        return "\n\n".join(pages)
    except ImportError:
        return "[PDF parsing requires pymupdf: pip install pymupdf]"
    except Exception as e:
        return f"[PDF parse error: {e}]"


async def _parse_docx(data: bytes) -> str:
    try:
        import io
        from docx import Document
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        return "[DOCX parsing requires python-docx: pip install python-docx]"
    except Exception as e:
        return f"[DOCX parse error: {e}]"


async def _parse_csv_xlsx(data: bytes, filename: str) -> str:
    try:
        import io
        import pandas as pd
        if filename.endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(data), engine="openpyxl")
        else:
            df = pd.read_csv(io.BytesIO(data))
        # Summary + first rows
        shape_info = f"Shape: {df.shape[0]} rows × {df.shape[1]} columns\nColumns: {', '.join(df.columns.tolist())}\n\n"
        preview = df.head(20).to_markdown(index=False)
        return shape_info + preview
    except ImportError:
        return "[CSV/XLSX parsing requires pandas: pip install pandas openpyxl]"
    except Exception as e:
        return f"[CSV/XLSX parse error: {e}]"


async def parse_file(
    data: bytes,
    filename: str,
    content_type: str,
    chunk_size: int = 1500,
) -> FileAttachment:
    """Parse raw file bytes into a FileAttachment with text chunks."""
    from core.config import settings

    ext = Path(filename).suffix.lower()
    overlap = getattr(settings, "localmind_chunk_overlap_tokens", 200)

    if ext == ".pdf" or content_type == "application/pdf":
        text = await _parse_pdf(data)
    elif ext == ".docx":
        text = await _parse_docx(data)
    elif ext in (".csv", ".xlsx"):
        text = await _parse_csv_xlsx(data, filename)
    else:
        # Plain text / code — decode as UTF-8
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception as e:
            text = f"[Could not read file: {e}]"

    chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    logger.info(f"[file_reader] parsed {filename}: {len(text)} chars → {len(chunks)} chunks")

    return FileAttachment(
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        chunks=chunks,
    )


async def file_task(message: str) -> ToolResult:
    """
    FILE_TASK dispatch handler — used when no file is attached but the user
    references a file by name. Returns a prompt to attach the file.
    """
    return ToolResult(
        content=(
            "To work with a file, please attach it using the paperclip button in the chat interface, "
            "or use `localmind ask --file <path>` in the CLI."
        ),
        risk=RiskLevel.LOW,
        source="file_reader",
    )


# Register
register_tool(
    Intent.FILE_TASK,
    file_task,
    description="Parse and analyze uploaded files: PDF, DOCX, CSV, XLSX, code, text",
    cost=0.01,
    latency_ms=300,
)
