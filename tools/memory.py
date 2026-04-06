"""
Memory Tool — stores and recalls facts across sessions.

Two-tier memory:
1. Session memory: Full conversation history in SQLite (handled by SessionStore)
2. Persistent memory: Key facts in ChromaDB vector store for semantic recall

v0.3 feature — this module is a placeholder in v0.1.
The run() function is wired but the ChromaDB integration is scaffolded for v0.3.
"""
from __future__ import annotations
import logging
from core.models import RiskLevel
from tools.base import ToolResult

logger = logging.getLogger(__name__)


async def run(input_data: dict, context: dict) -> ToolResult:
    """
    Memory tool entry point.

    Supported operations (v0.3):
    - store: Save a fact to persistent memory
    - recall: Retrieve relevant facts for the current query
    - forget: Delete a stored fact
    - list: List all stored facts

    v0.1 stub — returns a placeholder response.
    """
    # v0.3: Parse operation from input_data and dispatch
    # operation = input_data.get("operation", "recall")
    # query = input_data.get("query", context.get("message", ""))

    return ToolResult(
        content="Memory tool is available in v0.3. Session history is active.",
        risk=RiskLevel.LOW,
        source="memory",
        metadata={"status": "stub", "milestone": "v0.3"},
    )


async def recall_facts(query: str, session_id: str, top_k: int = 5) -> list[str]:
    """
    Retrieve the most relevant stored facts for a query.
    v0.3: Replace stub with ChromaDB semantic search.

    Args:
        query: The user's current message.
        session_id: Current session (for personalization).
        top_k: Number of facts to return.

    Returns:
        List of relevant fact strings.
    """
    # v0.3 implementation:
    # collection = chroma_client.get_collection("localmind_memory")
    # results = collection.query(query_texts=[query], n_results=top_k)
    # return results["documents"][0]
    return []


async def store_fact(fact: str, session_id: str) -> bool:
    """
    Store a fact in persistent memory.
    v0.3: Replace stub with ChromaDB upsert.
    """
    logger.info(f"[v0.3 stub] Would store fact: {fact[:80]}")
    return True
