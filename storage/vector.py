"""
Vector Store — ChromaDB-backed semantic memory.

Used for cross-session fact storage (v0.3).
Scaffolded in v0.1 so the interface is stable and importable.

Collection schema:
    collection: "localmind_memory"
    documents: fact strings
    metadatas: {session_id, timestamp, source}
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
        return self._client

    def _fact_id(self, fact: str) -> str:
        return hashlib.sha256(fact.encode()).hexdigest()[:16]

    async def store(self, fact: str, session_id: str, source: str = "user") -> bool:
        """Store a fact in the vector store."""
        client = self._get_client()
        if not client or not self._collection:
            return False
        try:
            fact_id = self._fact_id(fact)
            self._collection.upsert(
                documents=[fact],
                metadatas=[{
                    "session_id": session_id,
                    "timestamp": str(time.time()),
                    "source": source,
                }],
                ids=[fact_id],
            )
            return True
        except Exception as e:
            logger.error(f"Vector store failed: {e}")
            return False

    async def recall(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve semantically relevant facts for a query."""
        client = self._get_client()
        if not client or not self._collection:
            return []
        try:
            count = self._collection.count()
            if count == 0:
                return []
            results = self._collection.query(
                query_texts=[query],
                n_results=min(top_k, count),
            )
            return results["documents"][0] if results["documents"] else []
        except Exception as e:
            logger.error(f"Vector recall failed: {e}")
            return []

    async def forget(self, fact: str) -> bool:
        """Delete a specific fact."""
        client = self._get_client()
        if not client or not self._collection:
            return False
        try:
            self._collection.delete(ids=[self._fact_id(fact)])
            return True
        except Exception as e:
            logger.error(f"Vector delete failed: {e}")
            return False

    async def list_all(self) -> list[str]:
        """Return all stored facts (for memory viewer UI)."""
        client = self._get_client()
        if not client or not self._collection:
            return []
        try:
            results = self._collection.get()
            return results["documents"] or []
        except Exception:
            return []
