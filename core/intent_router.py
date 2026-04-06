"""
Intent Router — classifies each incoming message to decide which tool (if any) to invoke.

v0.2 upgrades:
- Multi-intent detection: returns primary + secondary intents
- Explicit classify_multi() API for the agent loop
- Better pattern coverage (reduced false positives on search patterns)
- Documents what the "LLM fallback" would look like when wired in

Classification is rule-based first (fast, deterministic). In v0.3 an actual
lightweight model call will replace the fallback branch for ambiguous cases.
"""
from __future__ import annotations
import re
from core.models import Intent
from typing import Optional


# ── Rule-based patterns ──────────────────────────────────────────────────────
# Ordered within each group: most specific first.

_FILE_PATTERNS = [
    r"\b(read|open|parse|analyse|analyze|summarise|summarize|extract|explain)\s.{0,40}(file|document|pdf|doc|csv|code)\b",
    r"\b(file|document|pdf|docx|txt|csv|json|py|js|ts)\b",
]

# v0.2: tightened to reduce false positives on "what is X" questions
_SEARCH_PATTERNS = [
    r"\b(latest|current|recent|today|right now)\b",
    r"\b(search|look up|find out|what happened|news about|who is|who are)\b",
    r"\b(price of|stock price|weather in|sports score|election result)\b",
    r"\b(2025|2026)\b",  # year references strongly imply recency need
]

_CODE_PATTERNS = [
    r"\b(run|execute|compute|calculate)\b.{0,30}\b(code|script|function|this snippet)\b",
    r"\b(what (does|would|will) this (code|script|function) (do|output|return|print))\b",
]

_MEMORY_PATTERNS = [
    # 'save' and 'store' removed — those map to FILE_WRITE, not memory
    r"\b(remember|recall|forget|note that|don't forget|keep in mind)\b",
    r"\b(what did (i|we) (say|discuss|talk about|mention))\b",
    r"\b(from (our|the) (last|previous|earlier) (conversation|session|chat))\b",
    r"\b(you (told|said|mentioned|noted))\b",
]

_FILE_WRITE_PATTERNS = [
    r"\b(write|create|save|generate)\b.{0,30}\b(file|document|report|script)\b",
    r"\b(make me a|create a|write a)\b.{0,20}(\.(py|js|ts|txt|md|csv|json))\b",
    # "save the output to results.txt" — save/write + destination with extension
    r"\b(save|write|output)\b.{0,40}\b\w+\.(py|js|ts|txt|md|csv|json|html|sh|yaml|yml)\b",
]


def _matches(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


def classify(message: str, has_attachment: bool = False) -> Intent:
    """
    Classify a user message into a primary Intent.

    Args:
        message: The raw user message text.
        has_attachment: True if the user attached a file.

    Returns:
        The primary classified Intent.
    """
    primary, _ = classify_multi(message, has_attachment)
    return primary


def classify_multi(
    message: str,
    has_attachment: bool = False,
) -> tuple[Intent, Optional[Intent]]:
    """
    Classify a message into primary and optional secondary intent.

    Returns:
        (primary_intent, secondary_intent | None)

    Examples:
        "search for X and write it to a file"
        → (WEB_SEARCH, FILE_WRITE)

        "remember that I prefer Python"
        → (MEMORY_OP, None)

        "run this code and save the output"
        → (CODE_EXEC, FILE_WRITE)
    """
    if has_attachment:
        # A file write request WITH an attachment = process the file then maybe write
        if _matches(message, _FILE_WRITE_PATTERNS):
            return Intent.FILE_TASK, Intent.FILE_WRITE
        return Intent.FILE_TASK, None

    primary: Optional[Intent] = None
    secondary: Optional[Intent] = None

    # Evaluate all intents in priority order.
    # FILE_WRITE must come before FILE_TASK: "write to a file" is FILE_WRITE,
    # but the word "file" alone also matches FILE_TASK (broader pattern).
    # When both match, FILE_WRITE is the more specific intent.
    matches: list[Intent] = []

    if _matches(message, _MEMORY_PATTERNS):
        matches.append(Intent.MEMORY_OP)
    if _matches(message, _CODE_PATTERNS):
        matches.append(Intent.CODE_EXEC)
    if _matches(message, _SEARCH_PATTERNS):
        matches.append(Intent.WEB_SEARCH)
    if _matches(message, _FILE_WRITE_PATTERNS):
        matches.append(Intent.FILE_WRITE)
    if _matches(message, _FILE_PATTERNS) and Intent.FILE_WRITE not in matches:
        # Only add FILE_TASK if FILE_WRITE didn't already match (avoids duplicate file intents)
        matches.append(Intent.FILE_TASK)

    if not matches:
        return Intent.CHAT, None

    primary = matches[0]
    secondary = matches[1] if len(matches) > 1 else None

    return primary, secondary
