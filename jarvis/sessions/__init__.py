"""JARVIS session persistence (SQLite-backed).

Provides :class:`SessionStore` for persisting conversation history across
sessions using the Python stdlib ``sqlite3`` module — no extra dependencies.
"""

from .store import SessionStore

__all__ = ["SessionStore"]