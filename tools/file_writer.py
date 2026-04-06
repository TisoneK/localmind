"""
File Writer tool — creates files from model responses or tool results.

Handles FILE_WRITE intent. Writes content to the user's local filesystem
using a safe path within the configured output directory.
"""
from __future__ import annotations
import logging
import re
import time
from pathlib import Path

from core.models import ToolResult, RiskLevel

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("./localmind_output")


def _infer_filename(message: str) -> str:
    """Extract a filename from the user's request, or generate a timestamped one."""
    # Try to extract explicit filename
    match = re.search(
        r"\b([\w\-]+\.(py|js|ts|txt|md|csv|json|html|sh|yaml|yml))\b",
        message,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    # Generate a timestamped name
    ts = int(time.time())
    return f"localmind_output_{ts}.txt"


async def write_response(message: str, content: str) -> ToolResult:
    """
    Write content to a local file.

    Returns a ToolResult indicating success or failure.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = _infer_filename(message)
    filepath = OUTPUT_DIR / filename

    try:
        filepath.write_text(content, encoding="utf-8")
        logger.info(f"[file_writer] wrote {len(content)} chars to {filepath}")
        return ToolResult(
            content=f"File written successfully: {filepath}",
            risk=RiskLevel.LOW,
            source="file_writer",
            metadata={"path": str(filepath), "bytes": len(content.encode())},
        )
    except Exception as e:
        logger.error(f"[file_writer] failed: {e}")
        return ToolResult(
            content=f"Failed to write file: {e}",
            risk=RiskLevel.MEDIUM,
            source="file_writer",
        )
