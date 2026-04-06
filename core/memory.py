"""
Memory Composer — retrieval intelligence over the raw vector store.

Responsibilities:
- Decide WHEN to retrieve (only for memory-relevant intents)
- Apply a relevance threshold (cosine distance < 0.35 is meaningful)
- Blend recency and relevance when ranking facts
- Provide a clean interface to the engine: compose() → list[str]

Memory types supported:
    episodic  — things that happened ("last time you asked about X")
    semantic  — facts about the user or world ("user prefers Python")
    procedural — how-to knowledge (stored explicitly, retrieved by task)

All three are stored in ChromaDB with a `memory_type` metadata tag.
"""
from __future__ import annotations
import logging
import time
from typing import Optional

from core.models import Intent
from storage.vector import VectorStore

logger = logging.getLogger(__name__)

# Only retrieve memory for these intents — chat and file tasks benefit;
# code execution and web search have their own fresh context.
_MEMORY_RELEVANT_INTENTS = {
    Intent.CHAT,
    Intent.MEMORY_OP,
    Intent.FILE_TASK,
    Intent.FILE_WRITE,
}

# Similarity threshold: ChromaDB cosine distance, lower = more similar.
# 0.0 = identical, 1.0 = orthogonal. Facts above this threshold are noise.
_RELEVANCE_THRESHOLD = 0.45

# How many facts to surface maximum (keeps context pressure manageable)
_MAX_FACTS = 6

# Recency weight: facts from the last N seconds get a small score boost
_RECENCY_WINDOW_SECS = 3600 * 24 * 7  # 1 week


class MemoryComposer:
    """
    Retrieves and ranks memory facts for injection into the prompt.

    Usage:
        composer = MemoryComposer()
        facts = await composer.compose(query=message, intent=intent, session_id=sid)
        # facts is a list[str] — inject into EngineContext.memory_facts
    """

    def __init__(self, vector_store: Optional[VectorStore] = None):
        self._store = vector_store or VectorStore()

    async def compose(
        self,
        query: str,
        intent: Intent,
        session_id: str,
        top_k: int = _MAX_FACTS,
    ) -> list[str]:
        """
        Return a ranked list of relevant memory facts for this query.

        Returns empty list if:
        - Intent doesn't benefit from memory retrieval
        - No facts are stored yet
        - All retrieved facts are below the relevance threshold
        """
        if intent not in _MEMORY_RELEVANT_INTENTS:
            return []

        try:
            facts = await self._store.recall_with_scores(query=query, top_k=top_k * 2)
        except Exception as e:
            logger.warning(f"Memory recall failed: {e}")
            return []

        if not facts:
            return []

        # Filter by relevance threshold and apply recency boost
        ranked = []
        now = time.time()
        for fact, distance, metadata in facts:
            if distance > _RELEVANCE_THRESHOLD:
                continue  # too dissimilar — skip

            # Base score is inverse distance (0.0 distance → score 1.0)
            score = 1.0 - distance

            # Recency boost: up to +0.15 for facts within the recency window
            ts = float(metadata.get("timestamp", 0))
            age_secs = now - ts
            if age_secs < _RECENCY_WINDOW_SECS:
                recency_boost = 0.15 * (1.0 - age_secs / _RECENCY_WINDOW_SECS)
                score += recency_boost

            ranked.append((score, fact))

        # Sort by combined score descending
        ranked.sort(key=lambda x: x[0], reverse=True)

        return [fact for _, fact in ranked[:top_k]]

    async def store(
        self,
        fact: str,
        session_id: str,
        memory_type: str = "semantic",
        source: str = "user",
    ) -> bool:
        """Store a new fact. memory_type: 'episodic' | 'semantic' | 'procedural'"""
        return await self._store.store(
            fact=fact,
            session_id=session_id,
            source=source,
            extra_metadata={"memory_type": memory_type},
        )

    async def forget(self, fact: str) -> bool:
        """Remove a specific fact from memory."""
        return await self._store.forget(fact)

    async def list_all(self) -> list[str]:
        """Return all stored facts (for memory viewer / debugging)."""
        return await self._store.list_all()
