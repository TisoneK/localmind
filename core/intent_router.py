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
    r"\b(latest|current|recent|right now|breaking|live|trending)\b",
    r"\b(search for|look up|find out|what happened|news about|google)\b",
    r"\b(price of|stock price|weather in|sports score|election result|crypto price)\b",
    # Only trigger year search if combined with recency verb — bare years are not enough
    r"\b(in (2025|2026)|as of (2025|2026)|(2025|2026) (news|results|update|price|stats))\b",
    r"\b(who (won|lost|is leading|was elected|signed|announced))\b",
    r"\b(today['']?s|this week['']?s|this month['']?s|today|this week|this month)\b",
    r"\b(news|headlines|breaking news)\b",
]

# Patterns that look like search but are NOT — override search classification
_SEARCH_EXCLUSION_PATTERNS = [
    r"\b(what is|what are|how (does|do|to)|explain|define|tell me about)\b",
    r"\b(who is|who was|who were)\b(?!.{0,30}\b(now|currently|still|today)\b)",
    r"\bwhy (is|are|do|does|did|was|were)\b",
]

_CODE_PATTERNS = [
    r"\b(run|execute|compute|calculate)\b.{0,30}\b(code|script|function|this snippet)\b",
    r"\b(what (does|would|will) this (code|script|function) (do|output|return|print))\b",
]

_SHELL_PATTERNS = [
    r"\b(run|execute)\b.{0,30}\b(command|shell|bash|terminal|cmd)\b",
    r"\b(cat|ls|grep|find|git|pip|diff)\s+\S+",       # bare shell commands
    r"\b(read|show|open)\b.{0,30}\bsource\b",          # "read the source file"
    r"\b(fix|modify|edit|update|patch)\b.{0,30}\b(yourself|itself|your own|source|code)\b",  # self-repair
    r"\b(what('?s| is) in|show me|print)\b.{0,20}\b\w+\.(py|js|ts|yaml|yml|toml|env)\b",
]

_MEMORY_PATTERNS = [
    # 'save' and 'store' removed — those map to FILE_WRITE, not memory
    r"\b(remember|recall|forget|note that|don't forget|keep in mind)\b",
    r"\b(what did (i|we) (say|discuss|talk about|mention))\b",
    r"\b(from (our|the) (last|previous|earlier) (conversation|session|chat))\b",
    r"\b(you (told|said|mentioned|noted))\b",
]

# SYSINFO — offline answers via stdlib. Must be checked BEFORE search patterns
# so "what time is it" never triggers a web search.
_SYSINFO_PATTERNS = [
    r"\b(what (is|'?s) the (time|date|day|year|month))\b",
    r"\b(what time|current time|right now|today'?s date|what day)\b",
    r"\b(time (is it|now)|date (today|now|is it))\b",
    r"\b(my (pc|computer|machine|laptop|system) (spec|info|detail|hardware))\b",
    r"\b(how much (ram|memory|storage|disk)|cpu (speed|cores|info|usage))\b",
    r"\b(os version|operating system|system info|pc spec|computer spec)\b",
    r"\bwhat (os|processor|cpu|ram|memory|disk|storage) (do i have|am i running|is this)\b",
    r"\b(hostname|username|who am i|computer name)\b",
    # Installed programs
    r"\b(what (programs?|apps?|software|applications?|packages?) (are |do i have )?(installed|on (this|my)( pc| computer| machine)?)?)\b",
    r"\b(list|show|tell me).{0,20}(installed|programs?|software|apps?|applications?)\b",
    r"\b(is|are).{0,20}(installed|available).{0,20}(on this (pc|machine|computer))?\b",
]
# Keep them tight: only include messages that are unambiguously conversational
# and will NEVER need a tool. Anything not listed here falls through to normal
# rule-based + optional LLM classification.
_OBVIOUS_CHAT_PATTERNS = [
    # Greetings and social openers
    r"^(hi|hey|hello|howdy|yo|sup|greetings|good (morning|afternoon|evening|night))[\s!?.]*$",
    # Gratitude and acknowledgements
    r"^(thanks?|thank you|thx|ty|cheers|cool|ok|okay|got it|understood|noted|sure|np|no problem)[\s!?.]*$",
    # Farewells
    r"^(bye|goodbye|cya|see ya|later|take care|good night)[\s!?.]*$",
    # Very short conversational replies (≤ 4 words, no tool keywords)
    # Handled by length check in classify_multi, not a pattern
]

_FILE_WRITE_PATTERNS = [
    r"\b(write|create|save|generate)\b.{0,40}\b(file|document|report|script|note|readme)\b",
    r"\b(make me a|create a|write a|give me a|produce a)\b.{0,30}\b(script|program|tool|function|report|document)\b",
    # "save the output to results.txt" — save/write + destination with extension
    r"\b(save|write|output|export)\b.{0,40}\b\w+\.(py|js|ts|txt|md|csv|json|html|sh|yaml|yml)\b",
    # "write a python script", "create a bash script"
    r"\b(write|create|generate)\b.{0,20}\b(python|bash|shell|javascript|typescript|sql)\b.{0,20}\b(script|program|function|code)\b",
]


def _matches(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


def _matches_search(text: str) -> bool:
    """Search matching with exclusion layer to prevent false positives."""
    if not _matches(text, _SEARCH_PATTERNS):
        return False
    # If the message looks like a general knowledge question, don't search
    if _matches(text, _SEARCH_EXCLUSION_PATTERNS):
        return False
    return True


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

    Fast-path order:
      1. Obvious CHAT patterns (greetings, acks) → CHAT, no LLM needed
      2. Very short messages with no tool keywords → CHAT
      3. Attachment present → FILE_TASK
      4. Rule-based pattern matching → specific intent
      5. No match → CHAT (LLM classifier may refine this upstream)

    Returns:
        (primary_intent, secondary_intent | None)
    """
    stripped = message.strip()

    # ── Fast-path 1: obvious conversational messages ──────────────────────────
    # These are definitively CHAT — skip all further matching.
    if any(re.fullmatch(p, stripped, re.IGNORECASE) for p in _OBVIOUS_CHAT_PATTERNS):
        return Intent.CHAT, None

    # ── Fast-path 2: very short messages (≤ 6 words) with no tool signals ─────
    # "What time is it?", "Hello there", "How are you?" etc.
    words = stripped.split()
    if len(words) <= 6 and not has_attachment:
        has_tool_signal = (
            _matches(stripped, _SYSINFO_PATTERNS)
            or _matches(stripped, _MEMORY_PATTERNS)
            or _matches(stripped, _CODE_PATTERNS)
            or _matches_search(stripped)
            or _matches(stripped, _FILE_WRITE_PATTERNS)
            or _matches(stripped, _FILE_PATTERNS)
        )
        if not has_tool_signal:
            return Intent.CHAT, None

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

    if _matches(message, _SYSINFO_PATTERNS):
        matches.append(Intent.SYSINFO)
    if _matches(message, _MEMORY_PATTERNS):
        matches.append(Intent.MEMORY_OP)
    if _matches(message, _SHELL_PATTERNS):
        matches.append(Intent.SHELL)
    if _matches(message, _CODE_PATTERNS):
        matches.append(Intent.CODE_EXEC)
    if _matches_search(message):
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
