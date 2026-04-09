"""
Safety Gate -- minimal hard-block filter for LocalMind.

Only blocks content that is unambiguously harmful regardless of context.
Everything else is passed to the LLM, which has its own refusal capability.

Vocabulary-level keyword matching produces too many false positives to be
useful -- "crack a walnut", "drug policy", "kill the process" would all be
blocked by a broad blocklist. Keep this layer extremely tight.
"""
from __future__ import annotations
import re
from typing import Tuple

# Only patterns that are harmful in every possible context.
# Do NOT add general vocabulary like "hack", "drug", "kill", "steal" --
# those are common English words with many legitimate uses.
_HARD_BLOCKS: list[tuple[str, str]] = [
    (r"\b(csam|child pornography|child sexual abuse material)\b", "CSAM content"),
    (r"\b(make a bomb|build a bomb|synthesize\s+\w+\s+explosive)\b", "explosive synthesis"),
    (r"\b(bioweapon synthesis|nerve agent synthesis|sarin production)\b", "WMD content"),
]


def check(message: str) -> Tuple[bool, str]:
    """
    Hard-block check for incoming messages.

    Returns:
        (is_safe, reason) -- True if safe, False with reason if blocked.

    Only blocks content that is harmful in every conceivable context.
    Rely on the LLM's own safety training for everything else.
    """
    if not message or not message.strip():
        return False, "Empty message"

    message_lower = message.lower()
    for pattern, label in _HARD_BLOCKS:
        if re.search(pattern, message_lower, re.IGNORECASE):
            return False, f"Content blocked: {label}"

    return True, ""
