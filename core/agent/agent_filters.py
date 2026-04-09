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


def sanitize_thought_for_display(thought: str) -> str:
    """
    Remove agent-loop XML tags from a raw LLM thought string so that
    only the human-readable reasoning text is surfaced to the user.

    Handles:
    - Complete <action>...</action>, <reflect>..., <finish>..., <clarify>... blocks
    - Collapses resulting blank lines to at most two consecutive newlines
    """
    cleaned = _TAG_STRIP_PATTERN.sub("", thought).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


__all__ = [
    "sanitize_thought_for_display",
    "_filter_system_leaks",
    "_filter_tool_injection",
    "_filter_code_output",
]
