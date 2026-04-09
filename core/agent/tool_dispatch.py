"""
Tool dispatch with exponential-backoff retries.

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


async def dispatch_with_retry(
    tool_intent: Intent,
    tool_input: str,
    max_retries: int = TOOL_MAX_RETRIES,
    base_delay: float = TOOL_RETRY_BASE_DELAY,
) -> tuple[Optional[ToolResult], bool, int]:
    """
    Dispatch a tool with exponential-backoff retries.

    Returns:
        (result, failed, retry_count)
        - result:      ToolResult on success, None on exhausted retries
        - failed:      True if all attempts failed
        - retry_count: number of retries used (0 = succeeded first try)
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            result = await dispatch(tool_intent, tool_input)
            return result, False, attempt
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
