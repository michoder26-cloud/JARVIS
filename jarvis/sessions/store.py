"""SQLite-backed conversation persistence for JARVIS.

Uses only the Python stdlib ``sqlite3`` module — no extra dependencies.

Schema
------
sessions
    id          TEXT PRIMARY KEY
    created_at  TEXT (ISO 8601)
    updated_at  TEXT (ISO 8601)

messages
    id          INTEGER PRIMARY KEY AUTOINCREMENT
    session_id  TEXT (FK -> sessions.id)
    role        TEXT ('user' | 'assistant' | 'tool' | 'system')
    content     TEXT
    tool_calls  TEXT (JSON-serialized, optional)
    created_at  TEXT (ISO 8601)
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_DIR = Path(os.path.expanduser("~/.jarvis"))
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "sessions.db"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class SessionStore:
    """SQLite-backed conversation persistence.

    Parameters
    ----------
    db_path : str, optional
        Path to the SQLite database file. Defaults to ``~/.jarvis/sessions.db``.
        The parent directory and database file are created automatically if
        they don't exist.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = str(db_path) if db_path else str(DEFAULT_DB_PATH)
        # Ensure parent directory exists.
        parent = Path(self.db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        # Initialize schema (creates tables if missing).
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection with row factory enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Create tables if they don't already exist."""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL DEFAULT '',
                    tool_calls  TEXT,
                    created_at  TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id);
                """
            )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Session CRUD
    # ------------------------------------------------------------------ #
    def get_or_create(self, session_id: str = "default") -> Dict[str, Any]:
        """Get a session row, creating it if it doesn't exist.

        Returns
        -------
        dict
            Session row with keys: ``id``, ``created_at``, ``updated_at``.
        """
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is not None:
                return dict(row)
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
                (session_id, now, now),
            )
            conn.commit()
        return {"id": session_id, "created_at": now, "updated_at": now}

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions, newest first by ``updated_at``."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_session(self, session_id: str) -> None:
        """Clear all messages in a session (does not delete the session row)."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_now_iso(), session_id),
            )
            conn.commit()

    def decay_old_sessions(self, max_age_hours: int = 72) -> int:
        """Delete sessions older than ``max_age_hours``.

        Returns the number of sessions deleted.
        """
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=max_age_hours)
        ).isoformat()
        with self._connect() as conn:
            # Delete messages belonging to old sessions first.
            conn.execute(
                "DELETE FROM messages WHERE session_id IN ("
                "SELECT id FROM sessions WHERE updated_at < ?"
                ")",
                (cutoff,),
            )
            cur = conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount or 0

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #
    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: Optional[str] = None,
    ) -> int:
        """Save a message to the session.

        Ensures the session row exists, then inserts the message and bumps
        the session's ``updated_at``.

        Returns the new message row id.
        """
        self.get_or_create(session_id)
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages "
                "(session_id, role, content, tool_calls, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, tool_calls, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_messages(
        self, session_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get messages for a session, oldest first.

        Returns a list of dicts with keys: ``id``, ``session_id``, ``role``,
        ``content``, ``tool_calls``, ``created_at``.
        """
        self.get_or_create(session_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? "
                "ORDER BY id ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #
    def messages_as_history(
        self, session_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Return messages in LLM ``messages`` format: ``{role, content}``.

        Drops ``tool_calls``/``role == 'tool'`` entries for simplicity of
        replay into a fresh :class:`ConversationHistory`.
        """
        msgs = self.get_messages(session_id, limit=limit)
        out: List[Dict[str, Any]] = []
        for m in msgs:
            role = m.get("role")
            content = m.get("content") or ""
            if role in ("user", "assistant") and content:
                out.append({"role": role, "content": content})
        return out

    def __repr__(self) -> str:
        return f"<SessionStore db={self.db_path!r}>"