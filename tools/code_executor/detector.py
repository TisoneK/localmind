"""
Language detector — extracts code blocks and identifies the runtime.
Handles markdown fenced code blocks and bare code snippets.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class DetectedCode:
    language: str          # "python" | "javascript" | "unknown"
    code: str
    from_fence: bool       # True if extracted from a markdown code fence


_FENCE_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_+-]*)?\s*\n(?P<code>.*?)```",
    re.DOTALL,
)

_PYTHON_HINTS = {"python", "py"}
_JS_HINTS = {"javascript", "js", "node", "nodejs", "typescript", "ts"}


def detect(message: str) -> Optional[DetectedCode]:
    """
    Extract and identify code from a user message.

    Returns None if no executable code is found.
    """
    # Try fenced code blocks first
    match = _FENCE_RE.search(message)
    if match:
        lang_hint = (match.group("lang") or "").lower().strip()
        code = match.group("code").strip()
        language = _resolve_language(lang_hint)
        return DetectedCode(language=language, code=code, from_fence=True)

    # Heuristic: does the message contain Python-like or JS-like patterns?
    lower = message.lower()
    if any(kw in lower for kw in ["def ", "import ", "print(", "for ", "class "]):
        # Extract everything after common preamble phrases
        code = _strip_preamble(message)
        if code:
            return DetectedCode(language="python", code=code, from_fence=False)

    if any(kw in lower for kw in ["function ", "const ", "let ", "var ", "console.log"]):
        code = _strip_preamble(message)
        if code:
            return DetectedCode(language="javascript", code=code, from_fence=False)

    return None


def _resolve_language(hint: str) -> str:
    if hint in _PYTHON_HINTS:
        return "python"
    if hint in _JS_HINTS:
        return "javascript"
    return "python"  # default


def _strip_preamble(message: str) -> str:
    """Remove natural language before code."""
    patterns = [
        r"(run|execute|what (does|would|will) this (do|output|return|print)[^:]*:?)\s*",
        r"(output of|result of)[^:]*:\s*",
    ]
    for p in patterns:
        message = re.sub(p, "", message, flags=re.IGNORECASE)
    return message.strip()
