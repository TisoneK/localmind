"""
Vector Store — ChromaDB-backed semantic memory.

Upgraded in v0.2:
- recall_with_scores() returns (fact, distance, metadata) triples
- store() accepts extra_metadata for memory_type tagging
- session-scoped recall for episodic queries
- list_all() returns facts with metadata

Collection schema:
    collection: "localmind_memory"
    documents: fact strings
    metadatas: {session_id, timestamp, source, memory_type}
    ids: sha256(fact)[:16]
"""
from __future__ import annotations
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, persist_dir: str = None):
        self._persist_dir = persist_dir or settings.localmind_chroma_path
        self._client = None
        self._collection = None

    def _get_client(self):
        """Lazy-initialize ChromaDB client."""
        if self._client is None:
            try:
                import chromadb
                Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(path=self._persist_dir)
                self._collection = self._client.get_or_create_collection(
                    name="localmind_memory",
                    metadata={"hnsw:space": "cosine"},
                )
                logger.debug(f"ChromaDB ready at {self._persist_dir}")
            except ImportError:
                logger.warning("chromadb not installed. Memory persistence disabled.")
                return None
            except Exception as e:
                logger.error(f"ChromaDB init failed: {e}")
                return None
        return self._client

    def _fact_id(self, fact: str) -> str:
        return hashlib.sha256(fact.encode()).hexdigest()[:16]

    async def store(
        self,
        fact: str,
        session_id: str,
        source: str = "user",
        extra_metadata: Optional[dict] = None,
    ) -> bool:
        """Store a fact with optional extra metadata.

        A5: Before inserting, checks cosine similarity against existing facts.
        If any existing fact has distance < 0.15 (near-duplicate), we upsert
        onto that existing ID instead of creating a new entry. This prevents
        the same preference being stored multiple times.
        """
        client = self._get_client()
        if not client or not self._collection:
            return False
        try:
            metadata = {
                "session_id": session_id,
                "timestamp": str(time.time()),
                "source": source,
                "memory_type": "semantic",
            }
            if extra_metadata:
                metadata.update(extra_metadata)

            # A5: Deduplication — check for near-identical existing facts
            target_id = self._fact_id(fact)
            try:
                existing = self._collection.get()
                if existing.get("ids"):
                    dupes = self._collection.query(
                        query_texts=[fact],
                        n_results=min(3, len(existing["ids"])),
                        include=["distances"],
                    )
                    distances = dupes.get("distances", [[]])[0]
                    ids = dupes.get("ids", [[]])[0]
                    for dist, eid in zip(distances, ids):
                        if dist < 0.15:  # near-duplicate threshold
                            target_id = eid  # upsert onto existing fact's ID
                            logger.debug(f"[memory] dedup: dist={dist:.3f}, reusing id={eid[:8]}")
                            break
            except Exception:
                pass  # dedup check failed non-critically; proceed with normal store

            self._collection.upsert(
                documents=[fact],
                metadatas=[metadata],
                ids=[target_id],
            )
            return True
        except Exception as e:
            logger.error(f"Vector store failed: {e}")
            return False

    async def recall(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve semantically relevant facts (documents only)."""
        results = await self.recall_with_scores(query=query, top_k=top_k)
        return [fact for fact, _, _ in results]

    async def recall_with_scores(
        self,
        query: str,
        top_k: int = 10,
        session_id: Optional[str] = None,
    ) -> list[tuple[str, float, dict]]:
        """
        Retrieve facts with cosine distances and metadata.

        Returns:
            List of (fact, distance, metadata) tuples sorted by distance ascending.
            distance=0.0 is identical, distance=1.0 is orthogonal.
        """
        client = self._get_client()
        if not client or not self._collection:
            return []
        try:
            count = self._collection.count()
            if count == 0:
                return []

            where = {"session_id": session_id} if session_id else None
            query_kwargs = dict(
                query_texts=[query],
                n_results=min(top_k, count),
                include=["documents", "distances", "metadatas"],
            )
            if where:
                query_kwargs["where"] = where

            results = self._collection.query(**query_kwargs)
            docs = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            return list(zip(docs, distances, metadatas))
        except Exception as e:
            logger.error(f"Vector recall_with_scores failed: {e}")
            return []

    async def forget(self, fact: str) -> bool:
        """Delete a specific fact by content."""
        client = self._get_client()
        if not client or not self._collection:
            return False
        try:
            self._collection.delete(ids=[self._fact_id(fact)])
            return True
        except Exception as e:
            logger.error(f"Vector delete failed: {e}")
            return False

    async def forget_by_id(self, fact_id: str) -> bool:
        """Delete a specific fact by its ID (F4: memory viewer delete button)."""
        client = self._get_client()
        if not client or not self._collection:
            return False
        try:
            self._collection.delete(ids=[fact_id])
            return True
        except Exception as e:
            logger.error(f"Vector delete_by_id failed: {e}")
            return False

    async def list_all(self) -> list[str]:
        """Return all stored facts."""
        client = self._get_client()
        if not client or not self._collection:
            return []
        try:
            results = self._collection.get()
            return results.get("documents") or []
        except Exception:
            return []

    async def list_all_with_metadata(self) -> list[dict]:
        """Return all facts with metadata including their IDs (F4: memory viewer)."""
        client = self._get_client()
        if not client or not self._collection:
            return []
        try:
            results = self._collection.get(include=["documents", "metadatas"])
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []
            ids = results.get("ids") or []
            return [{"id": i, "fact": d, **m} for i, d, m in zip(ids, docs, metas)]
        except Exception:
            return []

    async def update_metadata(self, fact: str, updates: dict) -> bool:
        """Update metadata fields for an existing fact."""
        client = self._get_client()
        if not client or not self._collection:
            return False
        try:
            fact_id = self._fact_id(fact)
            # Get current metadata
            result = self._collection.get(ids=[fact_id], include=["metadatas"])
            existing_meta = result.get("metadatas", [{}])[0] if result.get("metadatas") else {}
            merged = {**existing_meta, **updates}
            self._collection.update(ids=[fact_id], metadatas=[merged])
            return True
        except Exception as e:
            logger.debug(f"Metadata update failed (non-critical): {e}")
            return False
