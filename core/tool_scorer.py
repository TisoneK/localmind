"""
Tool Scorer — uses registry metadata to rank and select tools intelligently.

Scoring formula (per audit feedback):
    score = relevance * 0.5 + (1/latency_norm) * 0.2 + (1/cost_norm) * 0.2 + reliability * 0.1

This converts the tool registry from a static lookup into a capability-aware
selection system. The engine queries this before dispatching any tool.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from core.models import Intent

logger = logging.getLogger(__name__)

# Known reliability scores per tool (0.0–1.0)
# Seeded with reasonable defaults; overridden at startup by DB stats (A4).
_RELIABILITY: dict[str, float] = {
    "web_search": 0.85,
    "code_exec":  0.90,
    "file_task":  0.95,
    "file_write": 0.95,
    "memory_op":  0.99,
    "chat":       1.00,
}


def load_reliability_from_db(db_reliability: dict[str, float]) -> None:
    """A4: Overwrite _RELIABILITY with DB-derived scores where sample size is sufficient.

    Call once at engine startup. Scores with insufficient samples fall back to defaults.
    """
    for tool_name, score in db_reliability.items():
        _RELIABILITY[tool_name] = round(score, 4)
        logger.debug(f"[tool scorer] loaded reliability {tool_name}={score:.3f} from DB")


def record_tool_outcome(store, tool_name: str, success: bool, latency_ms: int = 0) -> None:
    """A4: Persist tool outcome to DB and update in-memory reliability score.

    Args:
        store: SessionStore instance with record_tool_result().
        tool_name: Tool intent string (e.g. 'web_search').
        success: Whether the tool call succeeded.
        latency_ms: Measured latency for this call.
    """
    try:
        store.record_tool_result(tool_name, success, latency_ms)
        # Re-read updated reliability for this tool only
        updated = store.get_reliability()
        if tool_name in updated:
            _RELIABILITY[tool_name] = round(updated[tool_name], 4)
    except Exception as e:
        logger.debug(f"[tool scorer] reliability update skipped: {e}")

# Normalization ranges for latency (ms) and cost
_MAX_LATENCY_MS = 5000.0
_MAX_COST = 1.0


@dataclass
class ScoredTool:
    intent: Intent
    score: float
    description: str
    cost: float
    latency_ms: int
    reliability: float


def score_tools(
    available: list[dict],
    primary_intent: Intent,
    confidence: float = 1.0,
) -> list[ScoredTool]:
    """
    Score and rank available tools for the current request.

    Args:
        available: Tool metadata dicts from tools.available_tools()
        primary_intent: The classified primary intent
        confidence: LLM classifier confidence (lower = wider search)

    Returns:
        List of ScoredTool sorted by score descending.
    """
    scored = []
    for tool in available:
        tool_intent_str = tool["intent"]
        try:
            tool_intent = Intent(tool_intent_str)
        except ValueError:
            continue

        # Relevance: exact intent match = 1.0, else 0.0
        # With low confidence, open the relevance window slightly
        if tool_intent == primary_intent:
            relevance = 1.0
        elif confidence < 0.6:
            # Low-confidence: give adjacent intents a partial relevance score
            relevance = 0.3
        else:
            relevance = 0.0

        latency_ms = tool.get("latency_ms", 1000)
        cost = tool.get("cost", 0.5)
        reliability = _RELIABILITY.get(tool_intent_str, 0.8)

        latency_norm = min(latency_ms / _MAX_LATENCY_MS, 1.0)
        cost_norm = min(cost / _MAX_COST, 1.0)

        score = (
            relevance * 0.5
            + (1.0 - latency_norm) * 0.2
            + (1.0 - cost_norm) * 0.2
            + reliability * 0.1
        )

        scored.append(ScoredTool(
            intent=tool_intent,
            score=round(score, 4),
            description=tool.get("description", ""),
            cost=cost,
            latency_ms=latency_ms,
            reliability=reliability,
        ))

    scored.sort(key=lambda t: t.score, reverse=True)

    if scored:
        logger.debug(
            f"[tool scorer] top={scored[0].intent.value} "
            f"score={scored[0].score} confidence={confidence:.2f}"
        )

    return scored


def best_tool(
    available: list[dict],
    primary_intent: Intent,
    confidence: float = 1.0,
    min_score: float = 0.4,
) -> Intent | None:
    """
    Return the best-scoring tool intent, or None if nothing passes min_score.
    """
    ranked = score_tools(available, primary_intent, confidence)
    if not ranked or ranked[0].score < min_score:
        return None
    return ranked[0].intent
