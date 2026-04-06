"""
Session Store — SQLite-backed conversation history.

Schema:
    sessions(id TEXT PK, created_at REAL)
    messages(id INTEGER PK, session_id TEXT FK, role TEXT, content TEXT,
             tool_name TEXT, timestamp REAL)

No ORM — plain sqlite3 for zero extra dependencies and maximum transparency.
Migrations live in storage/migrations/ for forward compatibility.
"""
from __future__ import annotations
import sqlite3
import time
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Generator

from core.models import Message, Role

logger = logging.getLogger(__name__)


_SCHEMA = """
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
"""


class SessionStore:
    def __init__(self, db_path: str = "./localmind.db"):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        logger.debug(f"SQLite store ready at {self._path}")

    def ensure_session(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id) VALUES(?)",
                (session_id,),
            )

    def append(self, session_id: str, message: Message) -> None:
        self.ensure_session(session_id)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO messages(session_id, role, content, tool_name, timestamp)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    message.role.value,
                    message.content,
                    message.tool_name,
                    message.timestamp or time.time(),
                ),
            )

    def get_history(self, session_id: str, limit: int = 100) -> list[Message]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT role, content, tool_name, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

        return [
            Message(
                role=Role(row["role"]),
                content=row["content"],
                tool_name=row["tool_name"],
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    def list_sessions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.created_at,
                       COUNT(m.id) AS message_count,
                       MAX(m.timestamp) AS last_active
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY last_active DESC
                """,
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._conn() as conn:
            result = conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
        return result.rowcount > 0

    def clear_all(self) -> None:
        """Delete everything. Used in tests only."""
        with self._conn() as conn:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM sessions")
