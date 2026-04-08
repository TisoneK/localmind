"""
Code Execution tool — runs Python snippets in a subprocess.

Security model (honest):
- Runs in the SAME Python environment as LocalMind — full filesystem and
  network access. This is intentional for a local, trusted-user tool.
- Timeout enforced (default 30s from config) — runaway loops are killed.
- stdout/stderr captured and truncated to 4000 chars.
- NOT a sandbox. Do not expose to untrusted users without adding one
  (e.g. Docker, nsjail, or RestrictedPython).

Registered as Intent.CODE_EXEC in the tool registry.
"""
from __future__ import annotations
import asyncio
import logging
import re
import sys
import textwrap

from core.models import Intent, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)

OUTPUT_MAX = 4000
_CODE_FENCE = re.compile(r"```(?:python|py)?\s*([\s\S]+?)```", re.IGNORECASE)


def _extract_code(message: str) -> str:
    """
    Extract code from a fenced ```python block only.
    We do NOT try to guess code from unfenced natural language — that
    heuristic matches sentences like 'I want to import a file' and
    tries to execute them. If no fence is found, return empty string
    and let the caller surface a clear error.
    """
    match = _CODE_FENCE.search(message)
    if match:
        return match.group(1).strip()
    return ""


async def code_exec(message: str) -> ToolResult:
    from core.config import settings

    code = _extract_code(message)
    if not code:
        return ToolResult(
            content="No executable Python code found in the message. Please provide code in a ```python``` block.",
            risk=RiskLevel.LOW,
            source="code_exec",
        )

    timeout = getattr(settings, "localmind_code_exec_timeout", 30)
    enabled = getattr(settings, "localmind_code_exec_enabled", True)

    if not enabled:
        return ToolResult(
            content="Code execution is disabled. Set LOCALMIND_CODE_EXEC_ENABLED=true to enable.",
            risk=RiskLevel.LOW,
            source="code_exec",
        )

    # Write code to a temp string and run it as a subprocess
    safe_code = textwrap.dedent(code)

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", safe_code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(
                content=f"Code execution timed out after {timeout}s.",
                risk=RiskLevel.MEDIUM,
                source="code_exec",
                metadata={"exit_code": -1, "timed_out": True},
            )

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        parts = []
        if out:
            truncated = out[:OUTPUT_MAX]
            if len(out) > OUTPUT_MAX:
                truncated += f"\n… (truncated {len(out) - OUTPUT_MAX} chars)"
            parts.append(f"**stdout:**\n```\n{truncated}\n```")
        if err:
            truncated = err[:OUTPUT_MAX]
            parts.append(f"**stderr:**\n```\n{truncated}\n```")
        if not parts:
            parts.append("*(no output)*")

        content = "\n".join(parts)
        risk = RiskLevel.MEDIUM if exit_code != 0 else RiskLevel.LOW

        return ToolResult(
            content=content,
            risk=risk,
            source="code_exec",
            metadata={"exit_code": exit_code, "lines": len(out.splitlines())},
        )

    except Exception as e:
        logger.error(f"[code_exec] unexpected error: {e}")
        return ToolResult(
            content=f"Execution failed: {e}",
            risk=RiskLevel.HIGH,
            source="code_exec",
        )


# Register
register_tool(
    Intent.CODE_EXEC,
    code_exec,
    description="Execute Python code snippets and return stdout/stderr output",
    cost=0.02,
    latency_ms=2000,
    parallelizable=False,  # A3: stateful subprocess — must run sequentially
)
