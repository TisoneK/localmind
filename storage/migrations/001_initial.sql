-- Migration 001: Initial schema
-- Applied automatically by SessionStore._init_db() via CREATE TABLE IF NOT EXISTS
-- This file documents the canonical schema for reference and future migrations.

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT    PRIMARY KEY,
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT    NOT NULL CHECK(role IN ('user','assistant','system','tool')),
    content     TEXT    NOT NULL,
    tool_name   TEXT,
    timestamp   REAL    NOT NULL DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, timestamp);
