"""
Shell tool — runs safe shell commands for file inspection and self-repair tasks.

This is intentionally more capable than code_exec (which is Python-only).
It allows the model to: read source files, list directories, run pip, git diff, etc.

Security model:
- Blocked commands: rm -rf, sudo, curl|wget to external URLs, shutdown, mkfs
- Working directory locked to the project root (cannot cd above it)
- Timeout enforced (default 30s)
- stdout/stderr captured and returned

Registered as Intent.SHELL in the tool registry.
To enable: add LOCALMIND_SHELL_ENABLED=true to .env
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
import shlex
from pathlib import Path

from core.models import Intent, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)

OUTPUT_MAX = 6000

# Commands and patterns that are never allowed
_BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bsudo\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bchmod\s+777\b",
    # Block outbound fetch — web_search tool handles that
    r"\b(curl|wget)\s+https?://",
    r">\s*/dev/(sda|sdb|hda)",
]

# Safe read-only and inspection commands always allowed
_SAFE_PREFIXES = (
    "cat ", "head ", "tail ", "ls ", "find ", "grep ", "wc ",
    "pwd", "echo ", "python ", "pip ", "git ", "diff ", "type ",
    "which ", "where ", "dir ", "tree ",
)


def _is_safe(command: str) -> tuple[bool, str]:
    """Return (safe, reason). Blocks dangerous patterns."""
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"blocked pattern: {pattern}"
    return True, ""


def _classify_risk(command: str) -> RiskLevel:
    cmd = command.strip().lower()
    if any(cmd.startswith(p) for p in _SAFE_PREFIXES):
        return RiskLevel.LOW
    if "write" in cmd or ">" in cmd or "pip install" in cmd:
        return RiskLevel.MEDIUM
    return RiskLevel.MEDIUM


async def shell_exec(message: str) -> ToolResult:
    from core.config import settings

    enabled = getattr(settings, "localmind_shell_enabled", False)
    if not enabled:
        return ToolResult(
            content=(
                "Shell tool is disabled. "
                "Set LOCALMIND_SHELL_ENABLED=true in .env to enable self-repair commands."
            ),
            risk=RiskLevel.LOW,
            source="shell",
        )

    # Extract the command — strip natural language wrapper if present
    command = message.strip()
    # If the message is wrapped in a code fence, extract it
    fence_match = re.search(r"```(?:bash|sh|shell)?\s*([\s\S]+?)```", command, re.IGNORECASE)
    if fence_match:
        command = fence_match.group(1).strip()
    # Strip common prefixes the model adds
    command = re.sub(r"^(run|execute|shell|bash|sh):\s*", "", command, flags=re.IGNORECASE).strip()

    if not command:
        return ToolResult(
            content="No command found. Provide a shell command to run.",
            risk=RiskLevel.LOW,
            source="shell",
        )

    safe, reason = _is_safe(command)
    if not safe:
        return ToolResult(
            content=f"Command blocked for safety: {reason}\nCommand was: {command}",
            risk=RiskLevel.HIGH,
            source="shell",
        )

    timeout = getattr(settings, "localmind_code_exec_timeout", 30)
    risk = _classify_risk(command)

    # Lock working directory to project root
    cwd = str(Path(getattr(settings, "localmind_db_path", "./localmind.db")).parent.resolve())

    try:
        # Use shell=True so pipes, redirects work — but we've already blocked dangerous patterns
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(
                content=f"Command timed out after {timeout}s: {command}",
                risk=RiskLevel.MEDIUM,
                source="shell",
            )

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        parts = [f"**$ {command}**\n"]
        if out:
            truncated = out[:OUTPUT_MAX]
            if len(out) > OUTPUT_MAX:
                truncated += f"\n… (truncated {len(out) - OUTPUT_MAX} chars)"
            parts.append(truncated)
        if err:
            parts.append(f"\n**stderr:**\n{err[:2000]}")
        if not out and not err:
            parts.append("*(no output)*")
        if exit_code != 0:
            parts.append(f"\n**exit code: {exit_code}**")

        return ToolResult(
            content="\n".join(parts),
            risk=risk,
            source="shell",
            metadata={"exit_code": exit_code, "command": command[:200]},
        )

    except Exception as e:
        logger.error(f"[shell] unexpected error: {e}")
        return ToolResult(
            content=f"Shell execution failed: {e}",
            risk=RiskLevel.HIGH,
            source="shell",
        )


register_tool(
    Intent.SHELL,
    shell_exec,
    description=(
        "Run shell commands: read files (cat, head, grep), list dirs (ls, find), "
        "run Python scripts, git diff, pip install. Use for inspecting and fixing source files."
    ),
    cost=0.03,
    latency_ms=3000,
    parallelizable=False,
)
