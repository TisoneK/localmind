"""
Sandbox — runs code in an isolated subprocess with strict limits.

Security model:
- Subprocess inherits NO extra privileges
- Execution time capped by LOCALMIND_CODE_EXEC_TIMEOUT
- stdout/stderr captured, stdin closed
- Network access is NOT blocked at OS level (v0.1) — rely on model instructions
- File system access is NOT blocked — model instructions + confirmation UX cover this

v0.2 hardening: add seccomp/AppArmor profile on Linux, sandbox on macOS.
"""
from __future__ import annotations
import asyncio
import sys
import textwrap
import logging
from dataclasses import dataclass

from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self) -> str:
        parts = []
        if self.timed_out:
            parts.append(f"[Execution timed out after {settings.localmind_code_exec_timeout}s]")
        if self.stdout.strip():
            parts.append(f"Output:\n{self.stdout.strip()}")
        if self.stderr.strip():
            parts.append(f"Errors:\n{self.stderr.strip()}")
        if not parts:
            parts.append("[No output]")
        if self.exit_code != 0 and not self.timed_out:
            parts.append(f"[Exit code: {self.exit_code}]")
        return "\n\n".join(parts)


async def run_python(code: str) -> SandboxResult:
    """Execute Python code in a subprocess."""
    # Wrap in try/except so syntax errors surface cleanly
    wrapped = textwrap.dedent(f"""
import sys
try:
{textwrap.indent(code, '    ')}
except Exception as e:
    print(f"Error: {{type(e).__name__}}: {{e}}", file=sys.stderr)
    sys.exit(1)
""").strip()

    return await _run_subprocess(
        cmd=[sys.executable, "-c", wrapped],
        timeout=settings.localmind_code_exec_timeout,
    )


async def run_javascript(code: str) -> SandboxResult:
    """Execute JavaScript via Node.js."""
    wrapped = f"""
try {{
{code}
}} catch(e) {{
    process.stderr.write(e.toString() + '\\n');
    process.exit(1);
}}
""".strip()

    return await _run_subprocess(
        cmd=["node", "-e", wrapped],
        timeout=settings.localmind_code_exec_timeout,
    )


async def _run_subprocess(cmd: list[str], timeout: int) -> SandboxResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return SandboxResult(
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                exit_code=proc.returncode or 0,
                timed_out=False,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                timed_out=True,
            )
    except FileNotFoundError as e:
        runtime = cmd[0]
        return SandboxResult(
            stdout="",
            stderr=f"Runtime not found: {runtime}. Is it installed?",
            exit_code=127,
            timed_out=False,
        )
