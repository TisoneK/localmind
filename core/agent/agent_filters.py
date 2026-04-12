"""
Agent-level output filters.

Thin wrappers / additions on top of core.filters so that loop.py has a
single import point for all sanitization needs.
"""
from __future__ import annotations

import re

# Re-export core filters so callers only need one import
from core.filters import (
    _filter_system_leaks,
    _filter_tool_injection,
    _filter_code_output,
)

# Strip structured XML agent tags from raw thought before displaying to user.
_TAG_STRIP_PATTERN = re.compile(
    r"<(action|finish|reflect|clarify)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)

# Strip JSON agent protocol objects so they never leak to the UI.
# Matches any JSON object whose outermost key is a known agent protocol key.
# Uses a broad match (.*?) so it handles both single-line and multi-line cases.
_JSON_AGENT_PROTOCOL_PATTERN = re.compile(
    r'\{\s*"(?:action|finish|reflect|deliver_answer)".*?\}',
    re.DOTALL,
)


def sanitize_thought_for_display(thought: str) -> str:
    """
    Remove agent-loop protocol from a raw LLM thought string so that
    only human-readable reasoning text is surfaced to the user.

    Handles:
    - Complete <action>...</action>, <reflect>..., <finish>..., <clarify>... blocks
    - JSON agent protocol objects (action / finish / reflect / deliver_answer keys)
    - Collapses resulting blank lines to at most two consecutive newlines
    """
    cleaned = _TAG_STRIP_PATTERN.sub("", thought)
    cleaned = _JSON_AGENT_PROTOCOL_PATTERN.sub("", cleaned)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


__all__ = [
    "sanitize_thought_for_display",
    "_filter_system_leaks",
    "_filter_tool_injection",
    "_filter_code_output",
]
