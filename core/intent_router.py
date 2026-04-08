"""
Intent Router — classifies each incoming message to decide which tool (if any) to invoke.

v0.3 changes vs v0.2:
- Explicit priority values replace implicit append-order priority
- IntentRule dataclass co-locates patterns with their exclusions
- Search exclusion logic fixed: recency/currency signals override exclusions
- Short-message fast-path removed (was fragile and redundant)
- Optional[Intent] replaced with Intent | None (PEP 604, already using annotations)
- classify() simplified to a one-liner
- _matches_search() generalised into _rule_matches() used by all IntentRules
- primary return type is now Intent (non-optional); never None
- Inline doctest examples added for the most failure-prone patterns
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from core.models import Intent


# ── IntentRule ────────────────────────────────────────────────────────────────

@dataclass
class IntentRule:
    """
    Bundles everything needed to test one intent:

    - intent:     the Intent value to emit on match
    - patterns:   any match → candidate
    - exclusions: any match → suppress, UNLESS a strong_patterns match fires first
    - strong_patterns: recency/currency signals that override exclusions
    - priority:   higher = evaluated first; first match wins for primary intent
    """
    intent: Intent
    patterns: list[str]
    exclusions: list[str] = field(default_factory=list)
    strong_patterns: list[str] = field(default_factory=list)
    priority: int = 0

    def matches(self, text: str) -> bool:
        """
        Return True if this rule fires on *text*.

        Match logic:
          1. At least one pattern must match.
          2. If a strong_pattern also matches, exclusions are ignored → True.
          3. If an exclusion matches (and no strong_pattern did), → False.
          4. Otherwise → True.

        >>> rule = RULES_BY_INTENT[Intent.WEB_SEARCH]
        >>> rule.matches("what is the current Bitcoin price")
        True
        >>> rule.matches("what is recursion")
        False
        >>> rule.matches("latest AI news today")
        True
        """
        t = text.lower()
        if not any(re.search(p, t) for p in self.patterns):
            return False
        if self.strong_patterns and any(re.search(p, t) for p in self.strong_patterns):
            return True
        if self.exclusions and any(re.search(p, t) for p in self.exclusions):
            return False
        return True


# ── Pattern lists ─────────────────────────────────────────────────────────────

_SYSINFO_PATTERNS = [
    r"\b(what (is|'?s) the (time|date|day|year|month))\b",
    r"\b(what time|current time|today'?s date|what day)\b",
    r"\b(time (is it|now)|date (today|now|is it))\b",
    r"\b(my (pc|computer|machine|laptop|system) (spec|info|detail|hardware))\b",
    r"\b(how much (ram|memory|storage|disk)|cpu (speed|cores|info|usage))\b",
    r"\b(os version|operating system|system info|pc spec|computer spec)\b",
    r"\bwhat (os|processor|cpu|ram|memory|disk|storage) (do i have|am i running|is this)\b",
    r"\b(hostname|username|who am i|computer name)\b",
    r"\b(what (programs?|apps?|software|applications?|packages?) (are |do i have )?(installed|on (this|my)( pc| computer| machine)?)?)\b",
    r"\b(list|show|tell me).{0,20}(installed|programs?|software|apps?|applications?)\b",
    r"\b(is|are).{0,20}(installed|available).{0,20}(on this (pc|machine|computer))?\b",
]

_MEMORY_PATTERNS = [
    r"\b(remember|recall|forget|note that|don't forget|keep in mind)\b",
    r"\b(what did (i|we) (say|discuss|talk about|mention))\b",
    r"\b(from (our|the) (last|previous|earlier) (conversation|session|chat))\b",
    r"\b(you (told|said|mentioned|noted))\b",
]

_SHELL_PATTERNS = [
    r"\b(run|execute)\b.{0,30}\b(command|shell|bash|terminal|cmd)\b",
    r"\b(cat|ls|grep|find|git|pip|diff)\s+\S+",
    r"\b(read|show|open)\b.{0,30}\bsource\b",
    r"\b(fix|modify|edit|update|patch)\b.{0,30}\b(yourself|itself|your own|source|code)\b",
    r"\b(what('?s| is) in|show me|print)\b.{0,20}\b\w+\.(py|js|ts|yaml|yml|toml|env)\b",
]

_CODE_PATTERNS = [
    r"\b(run|execute|compute|calculate)\b.{0,30}\b(code|script|function|this snippet)\b",
    r"\b(what (does|would|will) this (code|script|function) (do|output|return|print))\b",
]

# Signals strong enough to override general-knowledge exclusions.
# These indicate the user wants *current* information, not a cached answer.
_SEARCH_STRONG_PATTERNS = [
    r"\b(latest|current|recent|right now|breaking|live|trending)\b",
    r"\b(today['']?s|this week['']?s|this month['']?s)\b",
    r"\b(price of|stock price|weather in|sports score|election result|crypto price)\b",
    r"\b(in (2025|2026)|as of (2025|2026)|(2025|2026) (news|results|update|price|stats))\b",
]

_SEARCH_PATTERNS = [
    *_SEARCH_STRONG_PATTERNS,
    r"\b(search for|look up|find out|what happened|news about|google)\b",
    r"\b(who (won|lost|is leading|was elected|signed|announced))\b",
    r"\b(news|headlines|breaking news)\b",
    r"\b(today|this week|this month)\b",
]

# Suppress search for general-knowledge questions — but only when no
# strong/recency pattern fired (handled by IntentRule.matches logic).
_SEARCH_EXCLUSION_PATTERNS = [
    r"\b(what is|what are|how (does|do|to)|explain|define|tell me about)\b",
    r"\b(who is|who was|who were)\b",
    r"\bwhy (is|are|do|does|did|was|were)\b",
]

_FILE_WRITE_PATTERNS = [
    r"\b(write|create|save|generate)\b.{0,40}\b(file|document|report|script|note|readme)\b",
    r"\b(make me a|create a|write a|give me a|produce a)\b.{0,30}\b(script|program|tool|function|report|document)\b",
    r"\b(save|write|output|export)\b.{0,40}\b\w+\.(py|js|ts|txt|md|csv|json|html|sh|yaml|yml)\b",
    r"\b(write|create|generate)\b.{0,20}\b(python|bash|shell|javascript|typescript|sql)\b.{0,20}\b(script|program|function|code)\b",
]

_FILE_TASK_PATTERNS = [
    r"\b(read|open|parse|analyse|analyze|summarise|summarize|extract|explain)\s.{0,40}(file|document|pdf|doc|csv|code)\b",
    r"\b(file|document|pdf|docx|txt|csv|json|py|js|ts)\b",
]

# Greetings, acks, farewells — exact-match only.
_OBVIOUS_CHAT_PATTERNS = [
    r"^(hi|hey|hello|howdy|yo|sup|greetings|good (morning|afternoon|evening|night))[\s!?.]*$",
    r"^(thanks?|thank you|thx|ty|cheers|cool|ok|okay|got it|understood|noted|sure|np|no problem)[\s!?.]*$",
    r"^(bye|goodbye|cya|see ya|later|take care|good night)[\s!?.]*$",
]


# ── Rule registry (priority descending) ──────────────────────────────────────
# SYSINFO is checked before WEB_SEARCH so "what time is it" never hits the web.
# FILE_WRITE is checked before FILE_TASK so "write to a file" picks the more
# specific intent; FILE_TASK is then guarded by an exclusion for FILE_WRITE.

INTENT_RULES: list[IntentRule] = [
    IntentRule(
        intent=Intent.SYSINFO,
        patterns=_SYSINFO_PATTERNS,
        priority=10,
    ),
    IntentRule(
        intent=Intent.MEMORY_OP,
        patterns=_MEMORY_PATTERNS,
        priority=9,
    ),
    IntentRule(
        intent=Intent.SHELL,
        patterns=_SHELL_PATTERNS,
        priority=8,
    ),
    IntentRule(
        intent=Intent.CODE_EXEC,
        patterns=_CODE_PATTERNS,
        priority=7,
    ),
    IntentRule(
        intent=Intent.WEB_SEARCH,
        patterns=_SEARCH_PATTERNS,
        exclusions=_SEARCH_EXCLUSION_PATTERNS,
        strong_patterns=_SEARCH_STRONG_PATTERNS,   # override exclusions when present
        priority=6,
    ),
    IntentRule(
        intent=Intent.FILE_WRITE,
        patterns=_FILE_WRITE_PATTERNS,
        priority=5,
    ),
    IntentRule(
        intent=Intent.FILE_TASK,
        patterns=_FILE_TASK_PATTERNS,
        # Don't double-classify when FILE_WRITE already matched
        exclusions=_FILE_WRITE_PATTERNS,
        priority=4,
    ),
]

# Lookup by intent for direct access (e.g. doctests, unit tests)
RULES_BY_INTENT: dict[Intent, IntentRule] = {r.intent: r for r in INTENT_RULES}

# Pre-sorted descending by priority (stable sort, safe to call at import time)
_SORTED_RULES: list[IntentRule] = sorted(INTENT_RULES, key=lambda r: r.priority, reverse=True)


# ── Public API ────────────────────────────────────────────────────────────────

def classify(message: str, has_attachment: bool = False) -> Intent:
    """Return the primary Intent for *message*."""
    return classify_multi(message, has_attachment)[0]


def classify_multi(
    message: str,
    has_attachment: bool = False,
) -> tuple[Intent, Intent | None]:
    """
    Classify a message into a primary and optional secondary intent.

    Fast-path order:
      1. Exact-match CHAT patterns (greetings, acks, farewells) → CHAT
      2. Attachment present → FILE_TASK (+ FILE_WRITE if write patterns also match)
      3. Priority-ordered rule evaluation → first match = primary, second = secondary
      4. No rule matched → CHAT

    Returns:
        (primary_intent, secondary_intent | None)

    >>> classify_multi("hi")
    (<Intent.CHAT: 'chat'>, None)
    >>> classify_multi("what is the current price of ETH?")
    (<Intent.WEB_SEARCH: 'web_search'>, None)
    >>> classify_multi("what is recursion")
    (<Intent.CHAT: 'chat'>, None)
    >>> classify_multi("search for latest AI news")
    (<Intent.WEB_SEARCH: 'web_search'>, None)
    """
    stripped = message.strip()

    # ── Fast-path 1: unambiguous conversational openers ───────────────────────
    if any(re.fullmatch(p, stripped, re.IGNORECASE) for p in _OBVIOUS_CHAT_PATTERNS):
        return Intent.CHAT, None

    # ── Fast-path 2: attachment present ───────────────────────────────────────
    if has_attachment:
        secondary = Intent.FILE_WRITE if RULES_BY_INTENT[Intent.FILE_WRITE].matches(message) else None
        return Intent.FILE_TASK, secondary

    # ── Rule-based classification ─────────────────────────────────────────────
    matched: list[Intent] = [
        rule.intent for rule in _SORTED_RULES if rule.matches(message)
    ]

    if not matched:
        return Intent.CHAT, None

    primary = matched[0]
    secondary = matched[1] if len(matched) > 1 else None
    return primary, secondary