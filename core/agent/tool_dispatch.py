"""
Tool dispatch with exponential-backoff retries and per-tool timeouts.

Extracted from the monolithic agent.py so that retry logic can be tested
and adjusted independently of the main loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.models import Intent, ToolResult
from core.agent.constants import TOOL_MAX_RETRIES, TOOL_RETRY_BASE_DELAY
from tools import dispatch

logger = logging.getLogger(__name__)

# Error types where retrying the identical input is guaranteed to produce
# the same failure.  On these we return immediately rather than burning
# the full retry budget on pointless attempts.
_NON_RETRIABLE: frozenset[str] = frozenset({
    "invalid_input",   # bad/missing code fence, malformed path, etc.
    "permission",      # tool disabled — won't change between retries
    "not_found",       # file doesn't exist — won't appear by retrying
})

# Per-tool hard timeout in seconds.  Sourced from settings so operators can
# tune them via .env without touching code.  Falls back to a safe default so
# this module works even if settings are unavailable (e.g. in unit tests).
def _tool_timeout(intent: Intent) -> float:
    """Return the configured timeout for this tool intent, in seconds."""
    try:
        from core.config import settings
        mapping = {
            Intent.WEB_SEARCH:  settings.ollama_timeout_web_search,
            Intent.CODE_EXEC:   settings.localmind_code_exec_timeout,
            Intent.FILE_WRITE:  settings.ollama_timeout_file_write,
            Intent.FILE_TASK:   settings.ollama_timeout_file_task,
            Intent.SHELL:       settings.ollama_timeout_shell,
            Intent.SYSINFO:     settings.ollama_timeout_sysinfo,
            Intent.MEMORY_OP:   settings.ollama_timeout_memory_op,
        }
        return float(mapping.get(intent, settings.ollama_timeout_default))
    except Exception:
        return 60.0  # safe fallback


async def dispatch_with_retry(
    tool_intent: Intent,
    tool_input: str,
    max_retries: int = TOOL_MAX_RETRIES,
    base_delay: float = TOOL_RETRY_BASE_DELAY,
) -> tuple[Optional[ToolResult], bool, int]:
    """
    Dispatch a tool with exponential-backoff retries and a per-tool timeout.

    Returns:
        (result, failed, retry_count)
        - result:      ToolResult on success, None on exhausted retries
        - failed:      True if all attempts failed
        - retry_count: number of retries used (0 = succeeded first try)
    """
    timeout_secs = _tool_timeout(tool_intent)
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.wait_for(
                dispatch(tool_intent, tool_input),
                timeout=timeout_secs,
            )
            # Short-circuit: if the tool returned a structured non-retriable error,
            # return immediately — retrying the same input is guaranteed to fail
            # the same way and wastes the retry budget.
            if result is not None and not result.success and result.error_type in _NON_RETRIABLE:
                logger.info(
                    f"[agent.dispatch] tool={tool_intent.value} non-retriable "
                    f"error_type={result.error_type!r} — skipping retries"
                )
                return result, False, attempt
            return result, False, attempt
        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"tool {tool_intent.value} timed out after {timeout_secs:.0f}s"
            )
            logger.warning(
                f"[agent.dispatch] tool={tool_intent.value} attempt={attempt + 1} "
                f"timed out after {timeout_secs:.0f}s"
            )
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"[agent.dispatch] tool={tool_intent.value} attempt={attempt + 1} "
                    f"failed: {exc} — retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

    logger.error(
        f"[agent.dispatch] tool={tool_intent.value} exhausted {max_retries} retries: {last_error}"
    )
    return None, True, max_retries
