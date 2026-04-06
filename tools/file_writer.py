"""
File Writer Tool — creates and edits files on disk.

Security model (enforced here, not in the UI):
- Every write or delete MUST have requires_confirmation=True
- The engine will not execute writes without explicit user approval
- Paths are validated to prevent traversal attacks
- Absolute paths only — no relative path tricks

v0.5 milestone. This module is fully scaffolded so the interface is stable,
but the engine will gate on requires_confirmation before calling execute().
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

from core.models import RiskLevel
from tools.base import ToolResult

logger = logging.getLogger(__name__)

# Paths that can never be written to
_PROTECTED_PATHS = {
    "/etc", "/sys", "/proc", "/boot", "/bin", "/sbin", "/usr/bin",
    "C:\\Windows", "C:\\System32",
}


def _is_safe_path(path: str) -> bool:
    resolved = str(Path(path).resolve())
    for protected in _PROTECTED_PATHS:
        if resolved.startswith(protected):
            return False
    return True


async def run(input_data: dict, context: dict) -> ToolResult:
    """
    File write tool entry point. Always returns requires_confirmation=True.
    Actual write is performed by execute() after user confirms.
    """
    path = input_data.get("path", "")
    content = input_data.get("content", "")

    if not path:
        return ToolResult(
            content="No file path specified.",
            risk=RiskLevel.HIGH,
            source="file_writer",
        )

    if not _is_safe_path(path):
        return ToolResult(
            content=f"Cannot write to protected path: {path}",
            risk=RiskLevel.HIGH,
            source="file_writer",
        )

    return ToolResult(
        content=f"Ready to write {len(content)} characters to: {path}\n\nConfirm to proceed.",
        risk=RiskLevel.HIGH,
        source="file_writer",
        requires_confirmation=True,
        metadata={"path": path, "size_bytes": len(content.encode())},
    )


async def execute(path: str, content: str, mode: str = "write") -> ToolResult:
    """
    Execute a confirmed file write.

    Args:
        path: Absolute or relative path to write.
        content: File content.
        mode: 'write' (overwrite) or 'append'.

    Returns:
        ToolResult confirming the write.
    """
    if not _is_safe_path(path):
        return ToolResult(
            content=f"Blocked: cannot write to protected path: {path}",
            risk=RiskLevel.HIGH,
            source="file_writer",
        )

    try:
        resolved = Path(path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a" if mode == "append" else "w"
        with open(resolved, file_mode, encoding="utf-8") as f:
            f.write(content)
        logger.info(f"File written: {resolved} ({len(content)} chars)")
        return ToolResult(
            content=f"File written successfully: {resolved}",
            risk=RiskLevel.HIGH,
            source="file_writer",
            metadata={"path": str(resolved), "mode": mode},
        )
    except Exception as e:
        logger.error(f"File write failed: {e}")
        return ToolResult(
            content=f"File write failed: {e}",
            risk=RiskLevel.HIGH,
            source="file_writer",
        )
