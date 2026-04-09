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
# Use built-in generics with from __future__ import annotations

from core.models import Intent
from storage.vector import VectorStore

logger = logging.getLogger(__name__)

_MEMORY_RELEVANT_INTENTS = {
    Intent.CHAT,
    Intent.MEMORY_OP,
    Intent.FILE_TASK,
    Intent.FILE_WRITE,
}

# Skip embedding for very short conversational messages — no meaningful matches.
_MIN_QUERY_WORDS_FOR_MEMORY = 5

_RELEVANCE_THRESHOLD = 0.45
_LOOSE_THRESHOLD = 0.60     # used when memory store is small (< 10 facts)
# NOTE: Higher threshold = less strict filtering (looser matching)
_MAX_FACTS = 6
_RECENCY_WINDOW_SECS = 3600 * 24 * 7   # 1 week
_IMPORTANCE_DEFAULT = 0.5               # 0.0–1.0


class MemoryComposer:
    """
    Retrieves and ranks memory facts using 4-factor scoring.

    Score = similarity * 0.6 + recency * 0.2 + importance * 0.1 + usage_freq * 0.1
    """

    def __init__(self, vector_store: VectorStore | None = None):
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

        # Skip embedding for short CHAT queries — no meaningful matches.
        # Always run for MEMORY_OP, FILE_TASK, and FILE_WRITE.
        word_count = len(query.strip().split())
        if intent == Intent.CHAT and word_count < _MIN_QUERY_WORDS_FOR_MEMORY:
            return []

        try:
            raw = await self._store.recall_with_scores(query=query, top_k=top_k * 3)
        except Exception as e:
            logger.warning("Memory recall failed: %s", e)
            return []

        if not raw:
            return []

        # Use a looser threshold when store is small (prevents empty memory on new installs)
        # NOTE: Need actual store size, not query result count
        try:
            total_facts = await self._store.count()
        except Exception:
            total_facts = 0  # Fallback if count() fails
        threshold = _LOOSE_THRESHOLD if total_facts < 10 else _RELEVANCE_THRESHOLD

        now = time.time()
        ranked = []

        for fact, distance, metadata in raw:
            if distance > threshold:
                continue

            # Factor 1: similarity (cosine, inverted)
            # sqlite-vec distances are normalised to [0,1] in VectorStore
            similarity_score = max(0.0, 1.0 - distance)  # Clamp to avoid negative values

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
            # NOTE: update_metadata needs document ID, not fact text
            doc_id = meta.get("id")
            if not doc_id:
                logger.debug("No document ID in metadata for fact: %s", fact[:50])
                continue
            try:
                new_count = int(meta.get("access_count", 0)) + 1
                await self._store.update_metadata(doc_id, {"access_count": str(new_count)})
            except Exception as e:
                logger.debug("Metadata update failed for %s: %s", doc_id[:8], e)
                # metadata update is best-effort

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

    async def search(self, query: str, session_id: str, top_k: int = 3) -> list[object]:
        """Search for similar facts and return objects with content attribute for deduplication."""
        # Use the vector store directly to get fact+metadata tuples
        try:
            raw = await self._store.recall_with_scores(query=query, top_k=top_k)
        except Exception as e:
            logger.warning("Memory search failed: %s", e)
            return []
        
        # Create objects with content attribute
        results = []
        for fact, distance, metadata in raw:
            # Create a simple object with content attribute
            class FactResult:
                def __init__(self, content: str):
                    self.content = content
            results.append(FactResult(fact))
        
        return results

    async def list_all_with_metadata(self) -> list[dict]:
        return await self._store.list_all_with_metadata()
