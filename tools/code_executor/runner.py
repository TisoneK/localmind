"""
Code Executor — tool entry point.

Detects language, runs in sandbox, returns formatted result.
Disabled if LOCALMIND_CODE_EXEC_ENABLED=false in .env.
"""
from __future__ import annotations
import logging

from core.config import settings
from core.models import RiskLevel
from tools.base import ToolResult
from tools.code_executor.detector import detect
from tools.code_executor.sandbox import run_python, run_javascript

logger = logging.getLogger(__name__)


async def run(input_data: dict, context: dict) -> ToolResult:
    """
    Execute code extracted from the user message.

    Args:
        input_data: Dict with 'message' or 'query' key.
        context: Engine context dict.

    Returns:
        ToolResult with execution output.
    """
    if not settings.localmind_code_exec_enabled:
        return ToolResult(
            content="Code execution is disabled. Set LOCALMIND_CODE_EXEC_ENABLED=true to enable.",
            risk=RiskLevel.LOW,
            source="code_executor",
        )

    message = input_data.get("message", input_data.get("query", ""))
    detected = detect(message)

    if not detected:
        return ToolResult(
            content="No executable code found in the message. Please include a code block.",
            risk=RiskLevel.LOW,
            source="code_executor",
        )

    logger.info(f"Executing {detected.language} code ({len(detected.code)} chars)")

    if detected.language == "javascript":
        result = await run_javascript(detected.code)
    else:
        result = await run_python(detected.code)

    return ToolResult(
        content=result.summary(),
        risk=RiskLevel.MEDIUM,
        source="code_executor",
        metadata={
            "language": detected.language,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
    )
