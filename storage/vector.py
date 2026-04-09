"""
Vector Store — sqlite-vec backed semantic memory.

Replaces ChromaDB entirely. Uses sqlite-vec (vec0 virtual table) inside the
same localmind.db file as SessionStore, eliminating the separate chroma_db/
directory and the 10-30 second cold-start embedding load.

Embedding source: Ollama /api/embeddings endpoint (same Ollama process
already running for chat — no new services required).

Public API is identical to the old ChromaDB implementation:
    store()                 — persist a fact with metadata + dedup gate
    recall()                — semantic search, returns facts only
    recall_with_scores()    — semantic search, returns (fact, distance, meta)
    forget()                — delete by fact content
    forget_by_id()          — delete by ID
    list_all()              — all facts
    list_all_with_metadata()— all facts + metadata dicts
    count()                 — total fact count
    update_metadata()       — patch metadata fields on an existing fact

Distance metric: L2 (euclidean). sqlite-vec vec0 uses L2 by default.
Distances returned are normalised to [0,1] via dist_norm = min(raw/sqrt(dim), 1.0)
so that MemoryComposer's 0.45 / 0.15 thresholds remain valid unchanged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import struct
import time
from pathlib import Path
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_EMBED_MODEL = "nomic-embed-text"
_EMBED_TIMEOUT = 30


def _embed(text: str, base_url: str) -> list[float] | None:
    """Call Ollama /api/embeddings. Falls back to chat model on 404."""
    url = f"{base_url.rstrip('/')}/api/embeddings"
    try:
        resp = httpx.post(
            url,
            json={"model": _EMBED_MODEL, "prompt": text},
            timeout=_EMBED_TIMEOUT,
        )
        if resp.status_code != 200:
            resp = httpx.post(
                url,
                json={"model": settings.ollama_model, "prompt": text},
                timeout=_EMBED_TIMEOUT,
            )
        return resp.json().get("embedding")
    except Exception as exc:
        logger.warning("Embedding request failed: %s", exc)
        return None


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _norm(raw_l2: float, dim: int) -> float:
    return min(raw_l2 / math.sqrt(dim), 1.0)


class VectorStore:
    def __init__(self, db_path: str = None):
        self._db_path = db_path or settings.localmind_db_path or "./localmind.db"
        self._base_url = settings.ollama_base_url
        self._dim: int | None = None
        self._ready = False
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vector_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vector_facts (
                    id          TEXT PRIMARY KEY,
                    content     TEXT NOT NULL,
                    metadata    TEXT NOT NULL DEFAULT '{}',
                    created_at  REAL NOT NULL DEFAULT (unixepoch('now'))
                )
            """)
            conn.commit()
            row = conn.execute(
                "SELECT value FROM vector_meta WHERE key = 'dim'"
            ).fetchone()
            if row:
                self._dim = int(row["value"])
                self._ensure_vec_table(conn)
                self._ready = True
            conn.close()
            logger.debug("VectorStore ready at %s", self._db_path)
        except Exception as exc:
            logger.error("VectorStore init failed: %s", exc)

    def _ensure_vec_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vector_embeddings
            USING vec0(embedding float[{self._dim}])
        """)
        conn.commit()

    def _set_dim(self, conn: sqlite3.Connection, dim: int) -> None:
        self._dim = dim
        conn.execute(
            "INSERT OR REPLACE INTO vector_meta(key, value) VALUES ('dim', ?)",
            (str(dim),),
        )
        self._ensure_vec_table(conn)
        self._ready = True

    def _fact_id(self, fact: str) -> str:
        return hashlib.sha256(fact.encode()).hexdigest()[:16]

    async def store(
        self,
        fact: str,
        session_id: str,
        source: str = "user",
        extra_metadata: Optional[dict] = None,
    ) -> bool:
        vec = _embed(fact, self._base_url)
        if vec is None:
            logger.warning("Embedding failed — fact not stored")
            return False

        metadata = {
            "session_id": session_id,
            "timestamp": str(time.time()),
            "source": source,
            "memory_type": "semantic",
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        try:
            conn = self._connect()
            if self._dim is None:
                self._set_dim(conn, len(vec))

            target_id = self._fact_id(fact)

            # Deduplication gate — mirror of old ChromaDB distance < 0.15 check
            try:
                blob = _pack(vec)
                rows = conn.execute(
                    """
                    SELECT vf.id, ve.distance
                    FROM vector_embeddings ve
                    JOIN vector_facts vf ON vf.rowid = ve.rowid
                    WHERE ve.embedding MATCH ? AND k = 3
                    ORDER BY ve.distance
                    """,
                    [blob],
                ).fetchall()
                for row in rows:
                    if _norm(row["distance"], self._dim) < 0.15:
                        target_id = row["id"]
                        logger.debug(
                            "[memory] dedup: dist=%.3f, reusing id=%s",
                            row["distance"], target_id[:8],
                        )
                        break
            except Exception:
                pass

            conn.execute(
                """
                INSERT INTO vector_facts(id, content, metadata)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content  = excluded.content,
                    metadata = excluded.metadata
                """,
                (target_id, fact, json.dumps(metadata)),
            )
            rowid = conn.execute(
                "SELECT rowid FROM vector_facts WHERE id = ?", (target_id,)
            ).fetchone()["rowid"]
            conn.execute("DELETE FROM vector_embeddings WHERE rowid = ?", (rowid,))
            conn.execute(
                "INSERT INTO vector_embeddings(rowid, embedding) VALUES (?, ?)",
                (rowid, _pack(vec)),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            logger.error("Vector store failed: %s", exc)
            return False

    async def recall(self, query: str, top_k: int = 5) -> list[str]:
        results = await self.recall_with_scores(query=query, top_k=top_k)
        return [fact for fact, _, _ in results]

    async def recall_with_scores(
        self,
        query: str,
        top_k: int = 10,
        session_id: Optional[str] = None,
    ) -> list[tuple[str, float, dict]]:
        if not self._ready:
            return []
        vec = _embed(query, self._base_url)
        if vec is None:
            return []
        try:
            conn = self._connect()
            total = conn.execute("SELECT COUNT(*) FROM vector_facts").fetchone()[0]
            if total == 0:
                conn.close()
                return []
            k = min(top_k, total)
            blob = _pack(vec)
            rows = conn.execute(
                """
                SELECT vf.id, vf.content, vf.metadata, ve.distance
                FROM vector_embeddings ve
                JOIN vector_facts vf ON vf.rowid = ve.rowid
                WHERE ve.embedding MATCH ? AND k = ?
                ORDER BY ve.distance
                """,
                [blob, k],
            ).fetchall()
            conn.close()
            results = []
            for row in rows:
                meta = json.loads(row["metadata"])
                if session_id and meta.get("session_id") != session_id:
                    continue
                meta["id"] = row["id"]
                results.append((row["content"], _norm(row["distance"], self._dim), meta))
            return results
        except Exception as exc:
            logger.error("recall_with_scores failed: %s", exc)
            return []

    async def forget(self, fact: str) -> bool:
        return await self.forget_by_id(self._fact_id(fact))

    async def forget_by_id(self, fact_id: str) -> bool:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT rowid FROM vector_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM vector_embeddings WHERE rowid = ?", (row["rowid"],)
                )
                conn.execute("DELETE FROM vector_facts WHERE id = ?", (fact_id,))
                conn.commit()
            conn.close()
            return True
        except Exception as exc:
            logger.error("forget_by_id failed: %s", exc)
            return False

    async def list_all(self) -> list[str]:
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT content FROM vector_facts ORDER BY created_at"
            ).fetchall()
            conn.close()
            return [r["content"] for r in rows]
        except Exception:
            return []

    async def list_all_with_metadata(self) -> list[dict]:
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, content, metadata FROM vector_facts ORDER BY created_at"
            ).fetchall()
            conn.close()
            return [
                {"id": r["id"], "fact": r["content"], **json.loads(r["metadata"])}
                for r in rows
            ]
        except Exception:
            return []

    async def count(self) -> int:
        try:
            conn = self._connect()
            n = conn.execute("SELECT COUNT(*) FROM vector_facts").fetchone()[0]
            conn.close()
            return n
        except Exception:
            return 0

    async def update_metadata(self, fact_id: str, updates: dict) -> bool:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT metadata FROM vector_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            if not row:
                conn.close()
                return False
            meta = json.loads(row["metadata"])
            meta.update(updates)
            conn.execute(
                "UPDATE vector_facts SET metadata = ? WHERE id = ?",
                (json.dumps(meta), fact_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            logger.debug("update_metadata non-critical failure: %s", exc)
            return False
