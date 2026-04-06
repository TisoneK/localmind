"""
History Summarizer — A2

When conversation history exceeds 75% of the token budget, summarize the
oldest 50% of messages into a single compact system message, then replace
those messages with the summary.  This preserves important early context
(user preferences, task setup) that would otherwise be silently dropped.

Usage:
    from core.summarizer import maybe_compress_history

    messages = maybe_compress_history(messages, adapter, token_budget)
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.base import BaseAdapter

logger = logging.getLogger(__name__)

# Summarise when history fills this fraction of the budget
_COMPRESS_THRESHOLD = 0.75
# Summarise the oldest this fraction of history messages
_COMPRESS_FRACTION = 0.50

_SUMMARY_PROMPT = (
    "You are a conversation summarizer. "
    "Summarize the following conversation excerpt into a compact paragraph "
    "that preserves key facts, user preferences, decisions, and context that "
    "would be important for continuing the conversation. "
    "Be concise — aim for 3-6 sentences. Do not add commentary or preamble."
)


def _rough_token_count(messages: list[dict]) -> int:
    """UTF-8 byte based token estimate (mirrors context_builder fallback)."""
    total = 0
    for m in messages:
        total += len(m.get("content", "").encode("utf-8")) // 4 + 4
    return total


async def maybe_compress_history(
    messages: list[dict],
    adapter: "BaseAdapter",
    token_budget: int,
) -> list[dict]:
    """
    A2: If history exceeds 75% of token_budget, summarize oldest 50% into a
    single system message and return the compressed list.

    Args:
        messages: Full prompt message list (may include a system message at [0]).
        adapter:  LLM adapter used to generate the summary.
        token_budget: Model context window in tokens.

    Returns:
        Original list unchanged, or compressed list with a summary injected.
    """
    used = _rough_token_count(messages)
    threshold = int(token_budget * _COMPRESS_THRESHOLD)

    if used < threshold:
        return messages  # nothing to do

    # Separate system message (keep it intact) from conversation messages
    has_system = messages and messages[0]["role"] == "system"
    system_msgs = messages[:1] if has_system else []
    conv_msgs = messages[1:] if has_system else messages[:]

    if len(conv_msgs) < 4:
        return messages  # too short to compress meaningfully

    # Identify oldest slice to summarise
    n_to_compress = max(2, int(len(conv_msgs) * _COMPRESS_FRACTION))
    to_compress = conv_msgs[:n_to_compress]
    to_keep = conv_msgs[n_to_compress:]

    # Build a text excerpt for the summariser
    excerpt = "\n".join(
        f"{m['role'].upper()}: {m.get('content', '')[:500]}"
        for m in to_compress
    )

    try:
        summary_chunks = []
        async for chunk in adapter.chat(
            [
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": excerpt},
            ],
            temperature=0.2,
        ):
            summary_chunks.append(chunk.text)
        summary_text = "".join(summary_chunks).strip()
    except Exception as e:
        logger.warning(f"[summarizer] summary generation failed: {e}; skipping compression")
        return messages

    if not summary_text:
        return messages

    summary_msg = {
        "role": "system",
        "content": f"[Conversation summary — earlier context]\n{summary_text}",
    }

    compressed = system_msgs + [summary_msg] + to_keep
    new_count = _rough_token_count(compressed)
    logger.info(
        f"[summarizer] compressed {n_to_compress} messages → summary "
        f"({used} → {new_count} tokens approx)"
    )
    return compressed
