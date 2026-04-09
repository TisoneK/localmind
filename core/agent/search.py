"""
Web-search result helpers for the agent loop.

Two responsibilities:
  1. Truncate raw search output before it enters the LLM context window
     (prevents context bloat and LLM hanging on large results).
  2. Create an immediate extractive summary so the user gets fast feedback
     even when the full result file is large.
"""
from __future__ import annotations

import time

from core.agent.constants import (
    WEB_SEARCH_MAX_CHARS_PER_RESULT,
    WEB_SEARCH_MAX_RESULTS,
)


def truncate_web_search_results(search_content: str) -> str:
    """
    Truncate formatted search results to fit context-window limits.

    Expected input format (from web_search tool):
        1. **Title**
           https://url
           Description text…

        2. **Title**
           …

    Keeps at most WEB_SEARCH_MAX_RESULTS result blocks, each capped at
    WEB_SEARCH_MAX_CHARS_PER_RESULT characters.
    """
    lines = search_content.split("\n")
    result_number_prefix = tuple(f"{i}." for i in range(1, 20))

    truncated: list[str] = []
    current_result: list[str] = []
    result_count = 0

    def _flush(block: list[str]) -> None:
        nonlocal result_count
        if block and result_count < WEB_SEARCH_MAX_RESULTS:
            text = "\n".join(block)
            if len(text) > WEB_SEARCH_MAX_CHARS_PER_RESULT:
                text = text[:WEB_SEARCH_MAX_CHARS_PER_RESULT] + "…"
            truncated.append(text)
            result_count += 1

    for line in lines:
        if line.strip().startswith(result_number_prefix):
            _flush(current_result)
            current_result = [line]
        else:
            current_result.append(line)

    _flush(current_result)
    return "\n".join(truncated)


def create_extractive_summary(search_content: str, query: str) -> str:
    """
    Build a lightweight extractive summary from raw search results.

    Scans for bold-formatted result titles (``**Title**``) and URL lines,
    producing a Markdown bullet list.  Falls back to the first ten lines
    if no titles are found.

    No LLM call — this is pure string processing and runs instantly.
    """
    lines = search_content.split("\n")
    summary_points: list[str] = []
    last_title_added = False

    for line in lines:
        stripped = line.strip()

        # Result title: starts with a number and contains **text**
        if stripped.startswith(tuple(f"{i}." for i in range(1, 20))) and "**" in stripped:
            parts = stripped.split("**")
            if len(parts) >= 2:
                title = parts[1].strip()
                if title and len(summary_points) < 5:
                    summary_points.append(f"• {title}")
                    last_title_added = True
            continue

        # URL line immediately following a title → attach as source link
        if last_title_added and stripped.startswith("https://") and summary_points:
            summary_points[-1] += f" ([source]({stripped}))"
            last_title_added = False
        else:
            last_title_added = False

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"# Quick Summary: {query}\n\n*Generated: {timestamp}*\n\n"

    if summary_points:
        return header + "\n".join(summary_points[:5])

    # Fallback: first ten non-empty lines
    fallback_lines = [l for l in lines if l.strip()][:10]
    return header + "Key findings from search results:\n" + "\n".join(fallback_lines)
