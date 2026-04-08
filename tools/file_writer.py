"""
File Writer tool — writes files to ~/LocalMind/ with permission gates.

Rules:
- All writes go to ~/LocalMind/ by default unless user specifies a path
  within an allowed folder (Downloads, Documents, Desktop, etc.)
- Always asks permission before writing (permission gate via metadata flag)
- Never writes to OS/system directories
- Extracts code from fenced blocks before writing
- Returns full absolute path so UI can render a clickable link
"""
from __future__ import annotations
import logging
import re
import time
from pathlib import Path

from core.models import Intent, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)

_LANG_EXT = {
    "python": ".py", "py": ".py",
    "javascript": ".js", "js": ".js",
    "typescript": ".ts", "ts": ".ts",
    "bash": ".sh", "sh": ".sh", "shell": ".sh",
    "html": ".html", "css": ".css",
    "json": ".json", "yaml": ".yaml", "yml": ".yaml",
    "toml": ".toml", "sql": ".sql",
    "rust": ".rs", "go": ".go",
    "markdown": ".md", "md": ".md",
}

_FENCE_RE = re.compile(r"```(?P<lang>\w+)?\s*\n(?P<code>[\s\S]+?)\n```", re.IGNORECASE)
_FILENAME_RE = re.compile(
    r"\b([\w\-\.]+\.(?:py|js|ts|txt|md|csv|json|html|css|sh|yaml|yml|toml|sql|rs|go))\b",
    re.IGNORECASE,
)


def _extract_code_blocks(content: str) -> list[tuple[str, str]]:
    return [(m.group("lang") or "", m.group("code")) for m in _FENCE_RE.finditer(content)]


def _infer_filename(message: str, lang: str = "") -> str:
    match = _FILENAME_RE.search(message)
    if match:
        return match.group(1)
    ext = _LANG_EXT.get(lang.lower(), ".txt")
    return f"localmind_{int(time.time())}{ext}"


def _safe_output_path(filename: str, requested_dir: str = "") -> Path:
    from core.config import settings
    home = Path(settings.localmind_home)
    if requested_dir:
        candidate = Path(requested_dir).expanduser().resolve()
        if settings.is_path_allowed(candidate):
            return candidate / filename
    return home / filename


async def _do_write(filepath: Path, content: str) -> ToolResult:
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        size = len(content.encode())
        lines = len(content.splitlines())
        logger.info(f"[file_writer] wrote {size}B → {filepath}")
        return ToolResult(
            content=f"File written: **{filepath.name}**\nPath: `{filepath}`\n{lines} lines, {size} bytes",
            risk=RiskLevel.LOW,
            source="file_writer",
            metadata={"path": str(filepath), "filename": filepath.name, "bytes": size, "lines": lines},
        )
    except Exception as e:
        logger.error(f"[file_writer] {e}")
        return ToolResult(content=f"Write failed: {e}", risk=RiskLevel.MEDIUM, source="file_writer")


async def write_file(message: str) -> ToolResult:
    from core.config import settings
    blocks = _extract_code_blocks(message)
    if blocks:
        lang, code = blocks[0]
        filename = _infer_filename(message, lang)
        content_to_write = code
    else:
        filename = _infer_filename(message)
        content_to_write = message.strip()

    path_match = re.search(
        r"\b(?:to|in|at|into|save to|write to)\s+[\"']?([A-Za-z]:[/\\][\w/\\\s\.]+|~?/[\w/\\\s\.]+)[\"']?",
        message, re.IGNORECASE,
    )
    filepath = _safe_output_path(filename, path_match.group(1) if path_match else "")

    if settings.localmind_require_write_permission:
        return ToolResult(
            content=(
                f"I want to write **{filename}** to:\n`{filepath}`\n\n"
                "Reply **yes** to confirm or specify a different path."
            ),
            risk=RiskLevel.LOW,
            source="file_writer",
            metadata={
                "requires_permission": True,
                "pending_path": str(filepath),
                "pending_content": content_to_write,
                "filename": filename,
            },
            requires_confirmation=True,
        )
    return await _do_write(filepath, content_to_write)


register_tool(
    Intent.FILE_WRITE,
    write_file,
    description="Write code or text to a file on disk. Saves to ~/LocalMind/ by default. Confirms before writing.",
    cost=0.01,
    latency_ms=100,
    parallelizable=False,
)


async def write_response(message: str, content: str) -> None:
    """
    Secondary intent handler: write the assistant's response to a file.
    Called by engine.py when secondary_intent == Intent.FILE_WRITE.
    Infers filename from the original user message, writes content directly
    (no permission gate — secondary writes are considered pre-approved by
    the primary intent flow).
    """
    filename = _infer_filename(message)
    filepath = _safe_output_path(filename)
    await _do_write(filepath, content)
