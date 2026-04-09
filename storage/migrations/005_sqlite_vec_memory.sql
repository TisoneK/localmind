-- Migration 005: Replace ChromaDB with sqlite-vec
-- vector_facts and vector_embeddings are created by VectorStore._init_db()
-- using sqlite-vec extension. This migration documents the schema transition.
-- The chroma_db/ directory should be deleted from the project root.

CREATE TABLE IF NOT EXISTS vector_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vector_facts (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL DEFAULT (unixepoch('now'))
);

-- vector_embeddings is a vec0 virtual table created by VectorStore at runtime
-- after the embedding dimension is known. It cannot be pre-created here without
-- the sqlite-vec extension loaded, so VectorStore handles it directly.
