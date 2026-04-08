"""
LLM Intent Classifier — v0.4

Performance-first strategy:
  1. Run the fast rule-based classifier (intent_router) first.
  2. If the rule-based result is high-confidence (>= 0.80), return it immediately —
     no LLM call, no latency. This covers the vast majority of unambiguous messages
     like "What is the time?" (chat), "search for X" (web_search), etc.
  3. Only fall back to an LLM call for genuinely ambiguous messages where the
     rule-based router returns CHAT as a catch-all (low implicit confidence).
  4. The LLM classify call uses ollama_model_fast when configured, so it runs on
     a smaller model (e.g. phi3:mini) and completes much faster.

Output schema:
    {
        "primary": "web_search",
        "secondary": ["file_write"],   // list, may be empty
        "confidence": 0.92,
        "reasoning": "user asks for current info + wants to save it"
    }
"""
from __future__ import annotations
import json
import logging
import re
from typing import Optional

from core.models import Intent
from core import intent_router

logger = logging.getLogger(__name__)

_VALID_INTENTS = {i.value for i in Intent}

# Minimum rule-based confidence to skip the LLM call entirely
_RULE_CONFIDENCE_THRESHOLD = 0.60

# Rule-based confidence heuristic: non-CHAT matches are strong signals
_RULE_CONFIDENCE_BY_INTENT: dict[str, float] = {
    Intent.WEB_SEARCH.value:  0.72,
    Intent.CODE_EXEC.value:   0.92,
    Intent.SHELL.value:       0.90,
    Intent.SYSINFO.value:     0.95,  # very high — offline, always correct to use
    Intent.MEMORY_OP.value:   0.90,
    Intent.FILE_TASK.value:   0.90,
    Intent.FILE_WRITE.value:  0.88,
    Intent.CHAT.value:        0.85,
}

_CLASSIFIER_SYSTEM = """You are an intent classifier for a local AI assistant.

Classify the user message into one of these intents:
- chat: general conversation, questions answerable from knowledge alone
- web_search: needs current/live information, recent events, prices, news
- code_exec: run/execute code, compute output, evaluate a script
- memory_op: remember/recall/store user preferences or past info
- file_task: read, parse, summarize an existing file
- file_write: create, write, save content to a new file

Rules:
- Pick ONE primary intent (the main task)
- List any secondary intents (supporting tasks, max 1)
- Confidence: 0.0-1.0 how certain you are
- If message has a file attached (has_file=true), primary is usually file_task

Respond with ONLY valid JSON, no markdown, no explanation:
{"primary": "intent_name", "secondary": [], "confidence": 0.9, "reasoning": "brief reason"}"""


async def classify_with_llm(
    message: str,
    has_attachment: bool,
    adapter,
) -> tuple[Intent, Optional[Intent], float]:
    """
    Classify intent. Semantic first, then rule-based, then LLM fallback.

    Returns (primary, secondary, confidence).
    """
    # Step 1: Try semantic classification (future-proof)
    try:
        from core.semantic_classifier import classify_intent_semantic
        semantic_primary, semantic_secondary, semantic_confidence = classify_intent_semantic(message, has_attachment)
        
        # If semantic classification is confident, use it
        if semantic_confidence >= 0.75:  # Higher threshold for semantic
            logger.debug(
                f"[classifier] semantic match: {semantic_primary.value} "
                f"conf={semantic_confidence:.2f} (skipped rule-based + LLM)"
            )
            return semantic_primary, semantic_secondary, semantic_confidence
    except Exception as e:
        logger.debug(f"[classifier] semantic classification failed: {e}")

    # Step 2: Fast rule-based classification
    rule_primary, rule_secondary = intent_router.classify_multi(message, has_attachment)
    rule_confidence = _RULE_CONFIDENCE_BY_INTENT.get(rule_primary.value, 0.70)

    if rule_confidence >= _RULE_CONFIDENCE_THRESHOLD:
        # High confidence - skip LLM entirely, return immediately
        logger.debug(
            f"[classifier] rule-based shortcut: {rule_primary.value} "
            f"conf={rule_confidence:.2f} (skipped LLM)"
        )
        return rule_primary, rule_secondary, rule_confidence

    # ── Step 3: LLM classification for ambiguous cases ────────────────────
    # Use fast model if configured; fall back to main model
    from core.config import settings
    fast_model = getattr(settings, "ollama_model_fast", "")
    classify_adapter = adapter

    if fast_model and fast_model != settings.ollama_model:
        try:
            from adapters import get_adapter
            classify_adapter = get_adapter(settings.localmind_adapter, model_override=fast_model)
        except Exception:
            classify_adapter = adapter  # fall back silently

    user_content = f"has_file={str(has_attachment).lower()}\nmessage: {message}"
    messages = [
        {"role": "system", "content": _CLASSIFIER_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    try:
        chunks = []
        async for chunk in classify_adapter.chat(messages, temperature=0.0):
            chunks.append(chunk.text)

        raw = "".join(chunks).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        primary_str = data.get("primary", "").strip().lower()
        secondary_list = data.get("secondary", [])
        confidence = float(data.get("confidence", 0.5))

        if primary_str not in _VALID_INTENTS:
            raise ValueError(f"Unknown intent: {primary_str!r}")

        primary = Intent(primary_str)
        secondary = None
        if secondary_list:
            sec_str = secondary_list[0].strip().lower()
            if sec_str in _VALID_INTENTS and sec_str != primary_str:
                secondary = Intent(sec_str)

        logger.info(
            f"[llm classifier] primary={primary.value} secondary={secondary} "
            f"confidence={confidence:.2f} reasoning={data.get('reasoning', '')[:60]}"
        )
        return primary, secondary, confidence

    except Exception as e:
        logger.warning(f"[llm classifier] failed ({e}), using rule-based fallback")
        return rule_primary, rule_secondary, rule_confidence
