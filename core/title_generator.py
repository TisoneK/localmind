"""
Title Generation Subsystem

Provides deterministic and LLM-enhanced title generation for sessions.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

from adapters.ollama import OllamaAdapter
from storage.db import SessionStore
from core.config import settings

logger = logging.getLogger(__name__)


def generate_title_smart(message: str, intent: str | None = None) -> str:
    """Generate semantic, intent-aware title from user message."""
    if not message:
        return "New Chat"

    msg = message.lower()

    # Intent-driven titles (high signal)
    if intent == "file_processing":
        return "Document Analysis"

    if intent == "image_processing":
        return "Image Analysis"

    if intent == "web_search":
        return "News / Web Search"

    if intent == "code_generation":
        return "Code Generation"

    if intent == "chat":
        # fall through to semantic extraction
        pass

    # Pattern-based semantic extraction
    if "hello world" in msg:
        return "Hello World Program"

    if "news" in msg or "latest" in msg:
        return "Latest News"

    if "analyze" in msg or "analysis" in msg:
        return "Analysis Request"

    if "report" in msg:
        return "Report Generation"

    if "time" in msg and ("what" in msg or "current" in msg):
        return "Time Check"

    if "file" in msg or "attached" in msg:
        return "File Analysis"

    if "image" in msg or "jpeg" in msg or "png" in msg:
        return "Image Analysis"

    if "vs" in msg or "versus" in msg:
        # Extract matchup pattern
        words = message.split()
        if len(words) >= 3:
            for i, word in enumerate(words):
                if word.lower() in ["vs", "versus"]:
                    if i > 0 and i < len(words) - 1:
                        return f"{words[i-1]} vs {words[i+1]}"

    # Political/Government patterns
    if ("president" in msg or "government" in msg or "politics" in msg or 
        "election" in msg or "congress" in msg or "senate" in msg):
        return "Political Inquiry"

    if ("who" in msg and ("president" in msg or "leader" in msg)) or "current president" in msg:
        return "Presidential Question"

    # Time/Date patterns
    if ("what time" in msg or "current time" in msg) or ("time" in msg and ("what" in msg or "current" in msg)):
        return "Time Check"

    # Location/Geography patterns
    if ("where" in msg or "location" in msg or "address" in msg or "map" in msg):
        return "Location Query"

    # Definition/Explanation patterns
    if ("what is" in msg or "define" in msg or "explain" in msg or "meaning" in msg):
        return "Definition Request"

    # How-to patterns
    if ("how to" in msg or "how do" in msg or "steps" in msg or "tutorial" in msg):
        return "How-To Guide"

    # Comparison patterns
    if ("difference" in msg or "compare" in msg or "better" in msg or "versus" in msg):
        return "Comparison Analysis"

    # Fallback (cleaned + shortened)
    clean = message.strip()
    
    # Remove common prefixes
    prefixes = [
        "please", "kindly", "help me", "can you", "i want to",
        "could you", "would you", "i need", "can i", "how do i"
    ]
    for p in prefixes:
        if clean.lower().startswith(p):
            clean = clean[len(p):].strip()

    # Remove leading articles
    articles = ["a ", "an ", "the "]
    for article in articles:
        if clean.lower().startswith(article):
            clean = clean[len(article):].strip()

    # Truncate if too long
    if len(clean) > 40:
        clean = clean[:37] + "..."

    return clean.capitalize() if clean else "New Chat"


def generate_title_basic(text: str) -> str:
    """Legacy title generator - use generate_title_smart instead."""
    return generate_title_smart(text)


async def refine_title_async(session_id: str) -> None:
    """Asynchronously refine session title using LLM."""
    try:
        store = SessionStore(settings.localmind_db_path)
        messages = store.get_history(session_id, limit=3)  # Get first few messages
        
        if not messages:
            return

        # Use first user message for context
        first_user_msg = None
        for msg in messages:
            if msg.role.value == "user":
                first_user_msg = msg.content
                break
        
        if not first_user_msg:
            return

        prompt = f"""Summarize this user request in 3-6 words:

{first_user_msg}

Return only the title, nothing else."""

        ollama = OllamaAdapter()
        response = await ollama.generate(prompt)
        
        if response and response.strip():
            refined_title = response.strip()[:50]  # Limit length
            store.update_session_title(session_id, refined_title)
            logger.info(f"Refined title for session {session_id}: {refined_title}")
            
    except Exception as e:
        logger.warning(f"Failed to refine title for session {session_id}: {e}")


def should_generate_title(session_id: str, message_role: str) -> bool:
    """Check if title should be generated for this message."""
    if message_role != "user":
        return False
    
    try:
        store = SessionStore(settings.localmind_db_path)
        sessions = store.list_sessions()
        
        # Find current session
        current_session = None
        for session in sessions:
            if session["id"] == session_id:
                current_session = session
                break
        
        # Only generate if no title exists and this is the first user message
        if current_session and current_session.get("title") is None:
            return current_session.get("message_count", 0) <= 1
            
    except Exception as e:
        logger.warning(f"Error checking title generation: {e}")
    
    return False
