"""
Intent Router — classifies each incoming message to decide which tool (if any) to invoke.

Classification is rule-based first (fast, deterministic), with a fallback to
a lightweight model call for ambiguous cases. This keeps latency low for the
common case and accurate for edge cases.
"""
from __future__ import annotations
import re
from core.models import Intent, FileAttachment
from typing import Optional


# ── Rule-based patterns ──────────────────────────────────────────────────────
# Each pattern is checked in order. First match wins.
# Patterns are intentionally broad — false positives are cheap, false negatives
# mean the user doesn't get the tool they need.

_FILE_PATTERNS = [
    r"\b(read|open|parse|analyse|analyze|summarise|summarize|extract|what (is|does|are)|explain).{0,40}(file|document|pdf|doc|csv|code)\b",
    r"\b(file|document|pdf|docx|txt|csv|json|py|js|ts)\b",
]

_SEARCH_PATTERNS = [
    r"\b(latest|current|recent|today|now|2024|2025|2026)\b",
    r"\b(search|look up|find|what happened|who is|who are|news)\b",
    r"\b(price|stock|weather|score|result|update)\b",
]

_CODE_PATTERNS = [
    r"\b(run|execute|compute|calculate|output of|result of)\b.{0,30}\b(code|script|function|this)\b",
    r"\b(what (does|would|will) this (code|script|function) (do|output|return|print))\b",
]

_MEMORY_PATTERNS = [
    r"\b(remember|recall|forget|store|save|note that|don't forget)\b",
    r"\b(what did (i|we) (say|discuss|talk about))\b",
    r"\b(from (our|the) (last|previous|earlier) (conversation|session|chat))\b",
]

_FILE_WRITE_PATTERNS = [
    r"\b(write|create|save|generate).{0,30}(file|document|report|script)\b",
    r"\b(make me a|create a|write a).{0,20}(\.py|\.js|\.ts|\.txt|\.md|\.csv)\b",
]


def _matches(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


def classify(message: str, has_attachment: bool = False) -> Intent:
    """
    Classify a user message into an Intent.

    Args:
        message: The raw user message text.
        has_attachment: True if the user attached a file to this message.

    Returns:
        The classified Intent.
    """
    # File attachment always implies file task
    if has_attachment:
        return Intent.FILE_TASK

    if _matches(message, _FILE_WRITE_PATTERNS):
        return Intent.FILE_WRITE

    if _matches(message, _MEMORY_PATTERNS):
        return Intent.MEMORY_OP

    if _matches(message, _CODE_PATTERNS):
        return Intent.CODE_EXEC

    if _matches(message, _SEARCH_PATTERNS):
        return Intent.WEB_SEARCH

    if _matches(message, _FILE_PATTERNS):
        return Intent.FILE_TASK

    return Intent.CHAT
