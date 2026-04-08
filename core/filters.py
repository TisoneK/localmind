"""
Response filtering utilities — clean up system prompt leaks and tool injection attempts.

Two filters:
  _filter_system_leaks()    — strips structured agent XML tags that leaked into
                              final model output (action/finish/reflect/clarify blocks)
  _filter_tool_injection()  — neutralizes prompt injection patterns in tool outputs
                              before they re-enter the LLM context
"""
from __future__ import annotations
import re

# ── System leak filter ────────────────────────────────────────────────────────

# Structured tags used by the agent loop system prompt. These should never
# appear in the final user-facing response — if they do, the model leaked
# its internal reasoning format. Strip the entire tag + contents.
_AGENT_TAG_PATTERN = re.compile(
    r"<(action|finish|reflect|clarify)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)

# Bare closing/opening tags with no matching pair (malformed leaks)
_BARE_TAG_PATTERN = re.compile(
    r"</?(?:action|finish|reflect|clarify)\s*/?>",
    re.IGNORECASE,
)


def _filter_system_leaks(text: str) -> str:
    """
    Remove agent loop XML tags that leaked into model output.

    Handles:
    - Complete <action>...</action>, <reflect>...</reflect> etc. blocks
    - Bare/malformed tags without matching pairs
    - Inline occurrences (not just line-starts)

    Preserves:
    - User-facing thinking summaries streamed by the agent (### Reasoning, etc.)
    - All other model output
    """
    if not text:
        return text

    # Strip complete structured blocks first
    cleaned = _AGENT_TAG_PATTERN.sub("", text)
    # Strip any leftover bare tags
    cleaned = _BARE_TAG_PATTERN.sub("", cleaned)
    # Collapse runs of blank lines left by removal (max 2 consecutive)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


# ── Tool injection filter ─────────────────────────────────────────────────────

# Role prefixes only at line-start (with optional whitespace) are injection
# attempts. Mid-sentence "The system: Unix..." is legitimate content.
_ROLE_LINE_START = re.compile(
    r"^(\s*)(system|assistant|user)\s*:\s*",
    re.IGNORECASE | re.MULTILINE,
)

# XML-style instruction blocks — full block removal
_XML_INSTRUCTION_BLOCK = re.compile(
    r"<\s*(system|instruction|prompt)\s*>.*?</\s*\1\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Instruction override phrases — matched anywhere in text
_INJECTION_PHRASES = re.compile(
    r"(?:"
    r"ignore\s+(?:all\s+)?(?:previous\s+)?(?:instructions?|prompts?|rules?)"
    r"|override\s+(?:the\s+)?(?:system\s+)?(?:prompt|instructions?)"
    r"|forget\s+(?:everything|all\s+of\s+the\s+above|previous\s+context)"
    r"|disregard\s+(?:the\s+)?(?:above|previous\s+instructions?)"
    r"|you\s+are\s+now\s+(?:a\s+)?(?:different|new)\s+(?:ai|assistant|model)"
    r"|act\s+(?:as|like)\s+(?:a\s+)?(?:jailbreak|unrestricted|dan|evil|unfiltered)"
    r")",
    re.IGNORECASE,
)


def _filter_tool_injection(text: str, is_code_output: bool = False) -> str:
    """
    Neutralize prompt injection patterns in tool output before it re-enters
    the LLM context.

    Args:
        text: Raw tool output content.
        is_code_output: If True, skip code-pattern filters. Code execution
            output already went through a subprocess — exec()/eval() appearing
            in stdout are literal strings, not injection vectors. Filtering
            them would corrupt tracebacks and printed code.

    Preserves legitimate content:
    - Mid-sentence role words ("The system: Unix...", "user: noun")
    - Python builtins in code output (exec, eval, __import__)
    - Normal prose containing filtered words in non-injection context
    """
    if not text:
        return text

    # Strip XML instruction blocks (always — these are never legitimate in tool output)
    text = _XML_INSTRUCTION_BLOCK.sub("[content removed]", text)

    # Role prefixes at line-start only — mid-sentence occurrences are left alone
    text = _ROLE_LINE_START.sub(r"\1[\2]", text)

    # Instruction override phrases
    text = _INJECTION_PHRASES.sub("[filtered]", text)

    return text.strip()


def _filter_code_output(text: str) -> str:
    """
    Thin wrapper for code_exec output: applies injection filtering but
    explicitly skips code-pattern checks. Call this instead of
    _filter_tool_injection() for stdout/stderr from code execution.
    """
    return _filter_tool_injection(text, is_code_output=True)
    