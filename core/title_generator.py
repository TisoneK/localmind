"""
Title Generation Subsystem

Two-stage approach:
  Stage 1 (sync, instant): keyword/intent heuristic → placeholder title stored immediately
  Stage 2 (async, ~2s):    LLM call → concise title overwrites the placeholder

Stage 2 fires as a background asyncio task so it never blocks the response stream.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Stage 1: instant heuristic placeholder ────────────────────────────────────

def generate_title_smart(message: str, intent: str | None = None) -> str:
    """
    Fast synchronous placeholder title — used immediately so the sidebar
    shows something while the LLM-generated title is being computed.
    Returns a short cleaned version of the user message, max 35 chars.
    """
    if not message:
        return "New Chat"

    clean = message.strip()

    # Strip common leading filler words
    import re
    clean = re.sub(
        r"^(please|kindly|help me|can you|could you|would you|i need|i want to"
        r"|i'd like|tell me|show me|write me|give me|create a?|generate a?|make a?)\s+",
        "", clean, flags=re.IGNORECASE
    ).strip()

    # Strip leading articles
    clean = re.sub(r"^(a|an|the)\s+", "", clean, flags=re.IGNORECASE).strip()

    # Truncate
    if len(clean) > 35:
        # Try to cut at a word boundary
        cut = clean[:32].rsplit(" ", 1)[0]
        clean = cut + "…"

    return clean.capitalize() if clean else "New Chat"


# ── Stage 2: async LLM title ──────────────────────────────────────────────────

async def refine_title_async(session_id: str, message: str) -> None:
    """
    Generate a concise LLM title for the session and write it to the DB.
    Called as a fire-and-forget background task after the first turn completes.
    Uses the same Ollama adapter as the main engine — no separate HTTP client.
    """
    from storage.db import SessionStore
    from core.config import settings
    from adapters.ollama import OllamaAdapter

    try:
        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "You are a title generator. Given a user message, produce a short title "
                    "of 2–5 words that captures the topic. Output ONLY the title — no quotes, "
                    "no punctuation at the end, no explanation."
                ),
            },
            {"role": "user", "content": message[:300]},  # cap to avoid blowing context
        ]

        adapter = OllamaAdapter()
        chunks: list[str] = []
        async for chunk in adapter.chat(prompt_messages, temperature=0.3, intent="chat"):
            if chunk.text:
                chunks.append(chunk.text)

        raw = "".join(chunks).strip()

        # Sanitize: strip quotes, trailing punctuation, limit length
        import re
        raw = re.sub(r'^["\']|["\']$', "", raw).strip()
        raw = re.sub(r'[.!?]+$', "", raw).strip()
        if len(raw) > 50:
            raw = raw[:47] + "…"

        if not raw:
            return

        store = SessionStore(settings.localmind_db_path)
        store.update_session_title(session_id, raw)
        logger.info(f"[title] LLM title for {session_id[:8]}: '{raw}'")

    except Exception as exc:
        logger.warning(f"[title] refine_title_async failed: {exc}")


def should_generate_title(session_id: str, message_role: str) -> bool:
    """Legacy compat — no longer used in main path."""
    return message_role == "user"


def generate_title_basic(text: str) -> str:
    """Legacy compat."""
    return generate_title_smart(text)
