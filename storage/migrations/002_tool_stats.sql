-- Migration 002: Tool stats for dynamic reliability tracking (A4)
-- Applied automatically by SessionStore._init_db() via CREATE TABLE IF NOT EXISTS

CREATE TABLE IF NOT EXISTS tool_stats (
    tool_name        TEXT    PRIMARY KEY,
    success_count    INTEGER NOT NULL DEFAULT 0,
    failure_count    INTEGER NOT NULL DEFAULT 0,
    total_latency_ms INTEGER NOT NULL DEFAULT 0
);
