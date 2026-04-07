"""
Memory Composer — retrieval intelligence over the raw vector store.

v0.3 upgrades:
- 4-factor scoring: similarity, recency, importance, usage_frequency
- importance tag stored with each fact (user-rated or inferred)
- usage_frequency tracked via access_count metadata
- Low-confidence fallback: widen threshold when little memory exists

Scoring formula (per audit):
    score = similarity * 0.6 + recency * 0.2 + importance * 0.1 + usage_frequency * 0.1
"""
from __future__ import annotations
import logging
import time
from typing import Optional

from core.models import Intent
from storage.vector import VectorStore

logger = logging.getLogger(__name__)

_MEMORY_RELEVANT_INTENTS = {
    Intent.CHAT,
    Intent.MEMORY_OP,
    Intent.FILE_TASK,
    Intent.FILE_WRITE,
}

# Don't bother embedding and searching for very short conversational messages.
# These can't have meaningful memory matches and triggering ChromaDB here
# causes the cold-start embedding load (10-30s) on simple greetings.
_MIN_QUERY_WORDS_FOR_MEMORY = 5

_RELEVANCE_THRESHOLD = 0.45
_LOOSE_THRESHOLD = 0.60     # used when memory store is small (< 10 facts)
_MAX_FACTS = 6
_RECENCY_WINDOW_SECS = 3600 * 24 * 7   # 1 week
_IMPORTANCE_DEFAULT = 0.5               # 0.0–1.0


class MemoryComposer:
    """
    Retrieves and ranks memory facts using 4-factor scoring.

    Score = similarity * 0.6 + recency * 0.2 + importance * 0.1 + usage_freq * 0.1
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
        if intent not in _MEMORY_RELEVANT_INTENTS:
            return []

        # Skip embedding + vector search for short queries — ChromaDB cold-start
        # is expensive (10-30s) and short messages have no meaningful matches.
        # Always run for MEMORY_OP (explicit recall requests).
        word_count = len(query.strip().split())
        if intent != Intent.MEMORY_OP and word_count < _MIN_QUERY_WORDS_FOR_MEMORY:
            return []

        try:
            raw = await self._store.recall_with_scores(query=query, top_k=top_k * 3)
        except Exception as e:
            logger.warning(f"Memory recall failed: {e}")
            return []

        if not raw:
            return []

        # Use a looser threshold when the store is small (prevents empty memory on new installs)
        count = len(raw)
        threshold = _LOOSE_THRESHOLD if count < 10 else _RELEVANCE_THRESHOLD

        now = time.time()
        ranked = []

        for fact, distance, metadata in raw:
            if distance > threshold:
                continue

            # Factor 1: similarity (cosine, inverted)
            similarity_score = 1.0 - distance

            # Factor 2: recency (0.0–1.0, decays over the recency window)
            ts = float(metadata.get("timestamp", 0))
            age_secs = now - ts
            recency_score = max(0.0, 1.0 - age_secs / _RECENCY_WINDOW_SECS)

            # Factor 3: importance (stored in metadata, 0.0–1.0)
            importance = float(metadata.get("importance", _IMPORTANCE_DEFAULT))

            # Factor 4: usage frequency (access_count normalized, cap at 10)
            access_count = int(metadata.get("access_count", 0))
            usage_freq = min(access_count / 10.0, 1.0)

            score = (
                similarity_score * 0.6
                + recency_score * 0.2
                + importance * 0.1
                + usage_freq * 0.1
            )
            ranked.append((score, fact, metadata))

        ranked.sort(key=lambda x: x[0], reverse=True)

        # Increment access_count for retrieved facts (async fire-and-forget style)
        top_facts = [(fact, meta) for _, fact, meta in ranked[:top_k]]
        for fact, meta in top_facts:
            try:
                new_count = int(meta.get("access_count", 0)) + 1
                await self._store.update_metadata(fact, {"access_count": str(new_count)})
            except Exception:
                pass  # metadata update is best-effort

        return [fact for fact, _ in top_facts]

    async def store(
        self,
        fact: str,
        session_id: str,
        memory_type: str = "semantic",
        source: str = "user",
        importance: float = 0.5,
    ) -> bool:
        """Store a fact with importance rating (0.0=trivial, 1.0=critical)."""
        return await self._store.store(
            fact=fact,
            session_id=session_id,
            source=source,
            extra_metadata={
                "memory_type": memory_type,
                "importance": str(importance),
                "access_count": "0",
            },
        )

    async def forget(self, fact: str) -> bool:
        return await self._store.forget(fact)

    async def list_all(self) -> list[str]:
        return await self._store.list_all()

    async def list_all_with_metadata(self) -> list[dict]:
        return await self._store.list_all_with_metadata()
