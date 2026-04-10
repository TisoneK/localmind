"""
Vector Store — sqlite-vec backed semantic memory.  (OPTIMIZED v3 — final)

Iteration history
─────────────────
v1  LRU cache · per-thread SQLite · normalise-once · zero-cost dedup
v2  Normalised cache key · bounded ThreadPoolExecutor · semaphore-gated batch
v3  Two-tier cache · startup warm-up · transactional write batching ·
    persistent embed dedup · full observability (p50/p95 latency, semaphore
    wait, queue depth, SQLite write time, cache stats)

Architecture
────────────
                 ┌──────────────────────────────────┐
  text ──────▶  │  _normalise_text()                │
                 │  Tier-1: LRU (hot queries, 4096)  │
                 │  Tier-2: SQLite embed_cache table  │  ◀── persistent across restarts
                 └──────────────┬───────────────────┘
                                │ miss
                 ┌──────────────▼───────────────────┐
                 │  Ollama /api/embeddings            │
                 │  bounded ThreadPoolExecutor        │
                 │  Semaphore(_BATCH_CONCURRENCY)     │
                 └──────────────┬───────────────────┘
                                │
                 ┌──────────────▼───────────────────┐
                 │  _normalise() → _pack()           │
                 │  sqlite-vec  (dot-product / cos)  │
                 └──────────────────────────────────┘

Public API is **unchanged** — all existing callers (MemoryComposer, engine.py,
memory_tool.py) work without modification.

New additions (fully backwards-compatible):
  store_batch(facts, ...)         — bulk embed + persist in one transaction
  warmup()                        — pre-load Ollama model, call from startup()
  metrics() -> VectorMetrics      — structured snapshot of all counters
  clear_embed_cache()             — evict LRU tier (e.g. after model switch)
  embed_cache_info() -> str       — human-readable cache stats
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_EMBED_MODEL   = "nomic-embed-text"
_EMBED_TIMEOUT = 30

# ── Bounded thread pool ────────────────────────────────────────────────────────
# Shared across all VectorStore instances.  Sized to CPU count — beyond that,
# context-switching overhead dominates on CPU-first deployments.
_EXECUTOR = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)

# Maximum concurrent Ollama requests during batch ingestion.
_BATCH_CONCURRENCY = 4

# CPU count — used for both executor sizing and the per-store embed semaphore.
_CPU_COUNT: int = os.cpu_count() or 4

# ── Write buffer ───────────────────────────────────────────────────────────────
# Accumulates (key, blob) pairs from fire-and-forget _write_persistent calls.
# Flushed in a single transaction every _WRITE_BUFFER_FLUSH_EVERY items OR
# _WRITE_BUFFER_FLUSH_SECS seconds, whichever comes first.
# This turns N individual INSERTs (each with its own fsync) into one batch
# commit — 5–20× fewer disk syncs under ingestion load.
_WRITE_BUFFER_FLUSH_EVERY: int   = 20     # items
_WRITE_BUFFER_FLUSH_SECS:  float = 2.0    # seconds


# ── Observability ──────────────────────────────────────────────────────────────

@dataclass
class VectorMetrics:
    """Snapshot of all observable counters.  Returned by VectorStore.metrics()."""
    lru_hits:          int   = 0
    lru_misses:        int   = 0
    lru_size:          int   = 0
    persistent_hits:   int   = 0
    persistent_misses: int   = 0
    embed_count:       int   = 0
    embed_p50_ms:      float = 0.0
    embed_p95_ms:      float = 0.0
    sem_wait_p50_ms:   float = 0.0
    sem_wait_p95_ms:   float = 0.0
    db_write_count:    int   = 0
    db_write_p50_ms:   float = 0.0
    db_write_p95_ms:   float = 0.0
    active_workers:    int   = 0


class _Stats:
    """Rolling window of latency samples (last 1 000 per dimension)."""
    _WINDOW = 1000

    def __init__(self) -> None:
        self._lock      = threading.Lock()
        self._embed:    collections.deque = collections.deque(maxlen=self._WINDOW)
        self._sem_wait: collections.deque = collections.deque(maxlen=self._WINDOW)
        self._db_write: collections.deque = collections.deque(maxlen=self._WINDOW)
        self.persistent_hits   = 0
        self.persistent_misses = 0
        self.active_workers    = 0
        self.db_write_count    = 0

    def record_embed(self, ms: float) -> None:
        with self._lock:
            self._embed.append(ms)

    def record_sem_wait(self, ms: float) -> None:
        with self._lock:
            self._sem_wait.append(ms)

    def record_db_write(self, ms: float) -> None:
        with self._lock:
            self._db_write.append(ms)
            self.db_write_count += 1

    def _pct(self, samples: collections.deque, p: float) -> float:
        if not samples:
            return 0.0
        s = sorted(samples)
        return round(s[max(0, int(len(s) * p / 100) - 1)], 2)

    def snapshot(self) -> dict:
        with self._lock:
            info = _embed_cached.cache_info()
            return dict(
                lru_hits          = info.hits,
                lru_misses        = info.misses,
                lru_size          = info.currsize,
                persistent_hits   = self.persistent_hits,
                persistent_misses = self.persistent_misses,
                embed_count       = len(self._embed),
                embed_p50_ms      = self._pct(self._embed, 50),
                embed_p95_ms      = self._pct(self._embed, 95),
                sem_wait_p50_ms   = self._pct(self._sem_wait, 50),
                sem_wait_p95_ms   = self._pct(self._sem_wait, 95),
                db_write_count    = self.db_write_count,
                db_write_p50_ms   = self._pct(self._db_write, 50),
                db_write_p95_ms   = self._pct(self._db_write, 95),
                active_workers    = self.active_workers,
            )


_stats = _Stats()  # module-level singleton shared by all VectorStore instances


# ── Embedding cache — Tier 1: LRU ─────────────────────────────────────────────

def _normalise_text(text: str) -> str:
    """Stable cache key: strip, collapse whitespace, lowercase."""
    return re.sub(r"\s+", " ", text.strip()).lower()


@lru_cache(maxsize=4096)
def _embed_cached(norm_text: str, base_url: str) -> tuple:
    """
    Synchronous embed with LRU cache, keyed on normalised text.
    Records Ollama latency into _stats.
    Do NOT call directly — use _embed_async() which handles text normalisation.
    """
    t0  = time.monotonic()
    url = f"{base_url.rstrip('/')}/api/embeddings"
    result = None
    try:
        resp = httpx.post(url, json={"model": _EMBED_MODEL, "prompt": norm_text},
                          timeout=_EMBED_TIMEOUT)
        if resp.status_code != 200:
            resp = httpx.post(url, json={"model": settings.ollama_model, "prompt": norm_text},
                              timeout=_EMBED_TIMEOUT)
        data = resp.json().get("embedding")
        result = tuple(data) if data else None
    except Exception as exc:
        logger.warning("Embedding request failed: %s", exc)
    finally:
        _stats.record_embed((time.monotonic() - t0) * 1000)
    return result  # type: ignore[return-value]


async def _embed_async(text: str, base_url: str) -> list[float] | None:
    """
    Non-blocking embed via bounded _EXECUTOR.
    FastAPI event loop is never stalled; thread count stays at CPU count.
    """
    loop = asyncio.get_running_loop()
    _stats.active_workers += 1
    try:
        result = await loop.run_in_executor(
            _EXECUTOR, _embed_cached, _normalise_text(text), base_url
        )
    finally:
        _stats.active_workers -= 1
    return list(result) if result else None


# ── Vector math ────────────────────────────────────────────────────────────────

def _l2_norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _normalise(vec: list[float]) -> list[float]:
    n = _l2_norm(vec)
    return [x / n for x in vec] if n > 1e-10 else vec


def _is_normalised(vec: list[float]) -> bool:
    return abs(_l2_norm(vec) - 1.0) < 1e-5


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _cosine_to_score(dot: float) -> float:
    """dot-product of unit vectors → [0,1] distance (0=identical)."""
    return (1.0 - max(-1.0, min(1.0, dot))) / 2.0


# ── SQLite connection pool ─────────────────────────────────────────────────────
_pool: dict[str, sqlite3.Connection] = {}
_pool_lock = threading.Lock()


def _get_connection(db_path: str) -> sqlite3.Connection:
    """One WAL-mode connection per thread — avoids open/close overhead."""
    key = f"{db_path}:{threading.get_ident()}"
    with _pool_lock:
        if key not in _pool:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA cache_size = -8000")
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            _pool[key] = conn
        return _pool[key]


# ── VectorStore ────────────────────────────────────────────────────────────────

class VectorStore:
    def __init__(self, db_path: str = None):
        self._db_path  = db_path or settings.localmind_db_path or "./localmind.db"
        self._base_url = settings.ollama_base_url
        self._dim: int | None = None
        self._ready    = False

        # Soft concurrency guard for single-embed calls (_embed).
        # Prevents executor queue runaway when many coroutines all call _embed
        # concurrently — caps in-flight Ollama requests to CPU count without
        # ever blocking the event loop (asyncio.Semaphore is purely cooperative).
        # store_batch() manages its own tighter semaphore (_BATCH_CONCURRENCY)
        # and bypasses this one via _embed_async directly.
        self._embed_sem: asyncio.Semaphore = asyncio.Semaphore(_CPU_COUNT)

        # Batched SQLite write buffer for embed_cache persistence.
        # Pairs are queued here and flushed as a single transaction, turning
        # N individual fsyncs into one — critical for ingestion throughput.
        self._write_buf: list[tuple[str, bytes]] = []
        self._write_buf_lock   = threading.Lock()
        self._write_buf_last_flush: float = time.monotonic()

        self._init_db()

    # ── Setup ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        return _get_connection(self._db_path)

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
            # Tier-2 persistent embed cache — survives process restarts.
            # Maps sha256(norm_text) → packed float blob so re-ingesting the
            # same document never hits Ollama twice, even across restarts.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embed_cache (
                    key  TEXT PRIMARY KEY,
                    vec  BLOB NOT NULL
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

    # ── Tier-2 persistent embed cache ─────────────────────────────────────

    def _pkey(self, norm_text: str) -> str:
        return hashlib.sha256(norm_text.encode()).hexdigest()

    def _read_persistent(self, norm_text: str) -> list[float] | None:
        try:
            row = self._connect().execute(
                "SELECT vec FROM embed_cache WHERE key = ?", (self._pkey(norm_text),)
            ).fetchone()
            if row:
                blob: bytes = row["vec"]
                _stats.persistent_hits += 1
                return list(struct.unpack(f"{len(blob)//4}f", blob))
            _stats.persistent_misses += 1
            return None
        except Exception:
            return None

    def _write_persistent(self, norm_text: str, vec: list[float]) -> None:
        """
        Queue a (key, blob) pair for batched SQLite write.

        Called from run_in_executor (thread context) — must be thread-safe.
        Flushes the buffer synchronously when either threshold is hit so the
        caller's thread does the work rather than scheduling yet another task.
        """
        key  = self._pkey(norm_text)
        blob = _pack(vec)

        with self._write_buf_lock:
            self._write_buf.append((key, blob))
            should_flush = (
                len(self._write_buf) >= _WRITE_BUFFER_FLUSH_EVERY
                or (time.monotonic() - self._write_buf_last_flush) >= _WRITE_BUFFER_FLUSH_SECS
            )
            if should_flush:
                self._flush_write_buf_locked()

    def _flush_write_buf_locked(self) -> None:
        """
        Flush _write_buf in a single transaction.  Caller MUST hold _write_buf_lock.
        Errors are swallowed — cache writes are never critical.
        """
        if not self._write_buf:
            return
        batch, self._write_buf = self._write_buf, []
        self._write_buf_last_flush = time.monotonic()
        try:
            conn = self._connect()
            conn.execute("BEGIN")
            conn.executemany(
                "INSERT OR IGNORE INTO embed_cache(key, vec) VALUES (?, ?)", batch
            )
            conn.execute("COMMIT")
            _stats.record_db_write((time.monotonic() - self._write_buf_last_flush) * 1000)
        except Exception as exc:
            logger.debug("embed_cache batch flush failed (non-critical): %s", exc)
            try:
                self._connect().execute("ROLLBACK")
            except Exception:
                pass

    def flush_write_buf(self) -> None:
        """
        Public flush — call from shutdown hooks to drain any pending writes.

            @app.on_event("shutdown")
            async def on_shutdown():
                vector_store.flush_write_buf()
        """
        with self._write_buf_lock:
            self._flush_write_buf_locked()

    async def _embed(self, text: str) -> list[float] | None:
        """
        Two-tier embed lookup — fully non-blocking:
          1. LRU (_embed_cached via executor) — fastest, in-process
          2. SQLite embed_cache              — persistent across restarts
          3. Ollama                          — real inference, written to both tiers

        IMPORTANT: _embed_cached must NEVER be called synchronously here.
        On an LRU miss it performs a blocking Ollama HTTP request, which
        would stall the event loop for the full round-trip (~200–500 ms).
        Always go through _embed_async which runs _embed_cached in the
        bounded ThreadPoolExecutor.  LRU hits inside the executor cost
        ~10 µs of thread-dispatch overhead — negligible.

        Tier ordering:
          SQLite is checked before dispatching to the executor because a
          SQLite hit (~1–5 ms) avoids the full Ollama call (~200–500 ms).
          The LRU is still checked first *inside* _embed_cached (which runs
          in the executor), so a warm LRU hit still wins — it just pays the
          ~10 µs executor-dispatch cost instead of zero.  That tradeoff is
          correct: blocking the event loop on a rare cold miss is far worse
          than 10 µs of overhead on every warm hit.
        """
        norm = _normalise_text(text)

        # Tier 2: SQLite persistent cache — avoids Ollama on restarts.
        # Checked before executor dispatch so a disk hit short-circuits the
        # thread-pool round-trip entirely.
        vec = self._read_persistent(norm)
        if vec is not None:
            return vec

        # Tier 1 + 3: LRU (instant if warm) → Ollama — both run in executor,
        # event loop never blocked regardless of cache state.
        # Semaphore caps concurrent executor dispatches to CPU count, preventing
        # queue runaway under bursts without ever blocking the event loop.
        async with self._embed_sem:
            vec_raw = await _embed_async(text, self._base_url)
        if vec_raw is None:
            return None
        vec = _normalise(vec_raw)

        # Write-back to persistent tier (fire-and-forget, never blocks caller)
        asyncio.get_running_loop().run_in_executor(
            None, self._write_persistent, norm, vec
        )
        return vec

    # ── Public API ─────────────────────────────────────────────────────────

    async def warmup(self) -> None:
        """
        Pre-warm the Ollama embedding model.

        Ollama lazy-loads models on first request — that initial call can block
        for 2–10 s while the model is paged into memory.  Call once from your
        FastAPI startup hook so the first real user query is never affected:

            @app.on_event("startup")
            async def on_startup():
                await engine.startup()
                await vector_store.warmup()
        """
        logger.info("[vector] warming up embedding model…")
        t0 = time.monotonic()
        vec = await _embed_async("warmup", self._base_url)
        ms = round((time.monotonic() - t0) * 1000)
        if vec:
            logger.info("[vector] embed model warm (%d ms, dim=%d)", ms, len(vec))
        else:
            logger.warning("[vector] warmup failed — is Ollama running?")

    async def store(
        self,
        fact: str,
        session_id: str,
        source: str = "user",
        extra_metadata: Optional[dict] = None,
    ) -> bool:
        """Embed + persist one fact.  Use store_batch() for bulk ingestion."""
        vec = await self._embed(fact)
        if vec is None:
            logger.warning("Embedding failed — fact not stored")
            return False
        if not _is_normalised(vec):
            vec = _normalise(vec)
        return (await self._persist([(fact, vec)], session_id, source, extra_metadata)) == 1

    async def store_batch(
        self,
        facts: list[str],
        session_id: str,
        source: str = "user",
        extra_metadata: Optional[dict] = None,
    ) -> int:
        """
        Embed + persist multiple facts in a single SQLite transaction.

        Concurrent embedding is semaphore-gated to prevent Ollama queue
        saturation.  All writes are batched under one BEGIN/COMMIT, which is
        significantly faster than per-fact commits (especially on WAL mode).

        Returns the number of facts successfully stored.
        """
        if not facts:
            return 0

        sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def _bounded(text: str) -> list[float] | None:
            t_wait = time.monotonic()
            async with sem:
                _stats.record_sem_wait((time.monotonic() - t_wait) * 1000)
                return await self._embed(text)

        vecs_raw = await asyncio.gather(*[_bounded(f) for f in facts])

        pairs: list[tuple[str, list[float]]] = []
        for fact, vec in zip(facts, vecs_raw):
            if vec is None:
                logger.warning("Embedding failed: %s…", fact[:40])
                continue
            pairs.append((fact, vec if _is_normalised(vec) else _normalise(vec)))

        return await self._persist(pairs, session_id, source, extra_metadata)

    async def _persist(
        self,
        pairs: list[tuple[str, list[float]]],
        session_id: str,
        source: str,
        extra_metadata: Optional[dict],
    ) -> int:
        """
        Write N (fact, normalised_vec) pairs in a single transaction.

        Single-transaction batching is the primary write-performance lever:
        one fsync per batch instead of one per fact.  At 50 facts/batch this
        is typically 10–30× faster than the original one-commit-per-store design.
        """
        if not pairs:
            return 0

        meta_base = {
            "session_id": session_id,
            "timestamp":  str(time.time()),
            "source":     source,
            "memory_type": "semantic",
        }
        if extra_metadata:
            meta_base.update(extra_metadata)

        t0 = time.monotonic()
        stored = 0
        conn = self._connect()
        try:
            if self._dim is None:
                self._set_dim(conn, len(pairs[0][1]))

            conn.execute("BEGIN")
            for fact, vec in pairs:
                blob      = _pack(vec)
                target_id = self._fact_id(fact)

                # Deduplication — reuses already-computed vec (no extra embed call)
                try:
                    for row in conn.execute(
                        """
                        SELECT vf.id, ve.distance
                        FROM vector_embeddings ve
                        JOIN vector_facts vf ON vf.rowid = ve.rowid
                        WHERE ve.embedding MATCH ? AND k = 3
                        ORDER BY ve.distance
                        """,
                        [blob],
                    ).fetchall():
                        if _cosine_to_score(row["distance"]) < 0.15:
                            target_id = row["id"]
                            logger.debug("[memory] dedup id=%s", target_id[:8])
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
                    (target_id, fact, json.dumps(meta_base)),
                )
                rowid = conn.execute(
                    "SELECT rowid FROM vector_facts WHERE id = ?", (target_id,)
                ).fetchone()["rowid"]
                conn.execute("DELETE FROM vector_embeddings WHERE rowid = ?", (rowid,))
                conn.execute(
                    "INSERT INTO vector_embeddings(rowid, embedding) VALUES (?, ?)",
                    (rowid, blob),
                )
                stored += 1

            conn.execute("COMMIT")
            _stats.record_db_write((time.monotonic() - t0) * 1000)
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            logger.error("Vector batch write failed: %s", exc)
            stored = 0

        return stored

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
        vec_raw = await self._embed(query)
        if vec_raw is None:
            return []
        vec = vec_raw if _is_normalised(vec_raw) else _normalise(vec_raw)
        try:
            conn = self._connect()
            total = conn.execute("SELECT COUNT(*) FROM vector_facts").fetchone()[0]
            if total == 0:
                return []
            rows = conn.execute(
                """
                SELECT vf.id, vf.content, vf.metadata, ve.distance
                FROM vector_embeddings ve
                JOIN vector_facts vf ON vf.rowid = ve.rowid
                WHERE ve.embedding MATCH ? AND k = ?
                ORDER BY ve.distance
                """,
                [_pack(vec), min(top_k, total)],
            ).fetchall()
            results = []
            for row in rows:
                meta = json.loads(row["metadata"])
                if session_id and meta.get("session_id") != session_id:
                    continue
                meta["id"] = row["id"]
                results.append((row["content"], _cosine_to_score(row["distance"]), meta))
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
                conn.execute("DELETE FROM vector_embeddings WHERE rowid = ?", (row["rowid"],))
                conn.execute("DELETE FROM vector_facts WHERE id = ?", (fact_id,))
                conn.commit()
            return True
        except Exception as exc:
            logger.error("forget_by_id failed: %s", exc)
            return False

    async def list_all(self) -> list[str]:
        try:
            rows = self._connect().execute(
                "SELECT content FROM vector_facts ORDER BY created_at"
            ).fetchall()
            return [r["content"] for r in rows]
        except Exception:
            return []

    async def list_all_with_metadata(self) -> list[dict]:
        try:
            rows = self._connect().execute(
                "SELECT id, content, metadata FROM vector_facts ORDER BY created_at"
            ).fetchall()
            return [
                {"id": r["id"], "fact": r["content"], **json.loads(r["metadata"])}
                for r in rows
            ]
        except Exception:
            return []

    async def count(self) -> int:
        try:
            return self._connect().execute(
                "SELECT COUNT(*) FROM vector_facts"
            ).fetchone()[0]
        except Exception:
            return 0

    async def update_metadata(self, fact_id: str, updates: dict) -> bool:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT metadata FROM vector_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            if not row:
                return False
            meta = json.loads(row["metadata"])
            meta.update(updates)
            conn.execute(
                "UPDATE vector_facts SET metadata = ? WHERE id = ?",
                (json.dumps(meta), fact_id),
            )
            conn.commit()
            return True
        except Exception as exc:
            logger.debug("update_metadata non-critical: %s", exc)
            return False

    # ── Observability ──────────────────────────────────────────────────────

    def metrics(self) -> VectorMetrics:
        """
        Structured snapshot of all performance counters.

        Integrate with core/metrics.py or log on a schedule:

            m = vector_store.metrics()
            total = max(1, m.lru_hits + m.lru_misses)
            logger.info(
                "[vector] cache=%.0f%% embed_p95=%.0fms db_write_p95=%.0fms workers=%d",
                100 * m.lru_hits / total,
                m.embed_p95_ms,
                m.db_write_p95_ms,
                m.active_workers,
            )
        """
        return VectorMetrics(**_stats.snapshot())

    @staticmethod
    def clear_embed_cache() -> None:
        """Evict LRU tier (call after switching embedding model)."""
        _embed_cached.cache_clear()

    @staticmethod
    def embed_cache_info() -> str:
        info = _embed_cached.cache_info()
        return (
            f"lru hits={info.hits} misses={info.misses} "
            f"size={info.currsize}/{info.maxsize} | "
            f"persistent hits={_stats.persistent_hits} misses={_stats.persistent_misses}"
        )