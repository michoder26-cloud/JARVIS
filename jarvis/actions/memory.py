"""Persistent agent memory and reasoning scratchpad.

This module provides two tools:

* :func:`memory_manage` — read, add, update, and remove entries in a
  persistent memory file stored at ``~/.jarvis/MEMORY.md``.  The file uses
  simple markdown bullet points (``- entry``) so it is easy to inspect and
  edit by hand.

* :func:`think` — a reasoning scratchpad tool.  It simply echoes the
  thought back to the model; the content is never spoken to the user.  It
  gives the LLM a place to "think aloud" before acting on complex,
  multi-step commands.

Every function returns a dict with at least ``{"success": bool}``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Path to the persistent memory file.
MEMORY_DIR = Path(os.path.expanduser("~/.jarvis"))
MEMORY_FILE = MEMORY_DIR / "MEMORY.md"

__all__ = ["memory_manage", "think", "MEMORY_FILE", "MEMORY_DIR"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ensure_memory_file() -> None:
    """Create the memory directory and file if they don't exist."""
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if not MEMORY_FILE.exists():
            MEMORY_FILE.write_text(
                "# JARVIS Memory\n\nPersistent facts and preferences remembered across sessions.\n\n",
                encoding="utf-8",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not initialise memory file %s: %s", MEMORY_FILE, exc)


def _read_entries() -> list[str]:
    """Return the list of bullet-point entries currently stored in memory."""
    _ensure_memory_file()
    try:
        text = MEMORY_FILE.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read memory file: %s", exc)
        return []

    entries: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        # A memory entry is any line starting with "- " (bullet point).
        if stripped.startswith("- "):
            entries.append(stripped[2:].strip())
    return entries


def _write_entries(entries: list[str]) -> None:
    """Overwrite the memory file with the given list of entries."""
    _ensure_memory_file()
    body = "\n".join(f"- {e}" for e in entries)
    content = (
        "# JARVIS Memory\n\n"
        "Persistent facts and preferences remembered across sessions.\n\n"
        f"{body}\n"
    )
    MEMORY_FILE.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Public actions
# --------------------------------------------------------------------------- #
def memory_manage(
    action: str,
    entry: Optional[str] = None,
    new_entry: Optional[str] = None,
) -> dict:
    """Read, add, update, or remove entries in persistent memory.

    Parameters
    ----------
    action:
        One of ``"read"``, ``"add"``, ``"update"``, ``"remove"``.
    entry:
        The entry text.  Required for ``add`` / ``remove`` and used as the
        old text for ``update``.
    new_entry:
        The replacement text for the ``update`` action.

    Returns
    -------
    dict
        ``{"success": bool, "result": str | None, "error": str | None}``
    """
    action = (action or "").strip().lower()

    try:
        if action == "read":
            entries = _read_entries()
            if not entries:
                return {
                    "success": True,
                    "result": "Memory is empty.",
                    "error": None,
                }
            return {
                "success": True,
                "result": "\n".join(f"- {e}" for e in entries),
                "error": None,
            }

        if action == "add":
            if not entry or not entry.strip():
                return {
                    "success": False,
                    "result": None,
                    "error": "No entry provided for 'add' action.",
                }
            entry = entry.strip()
            entries = _read_entries()
            if entry in entries:
                return {
                    "success": True,
                    "result": f"Entry already exists: {entry}",
                    "error": None,
                }
            entries.append(entry)
            _write_entries(entries)
            return {
                "success": True,
                "result": f"Added: {entry}",
                "error": None,
            }

        if action == "update":
            if not entry or not entry.strip():
                return {
                    "success": False,
                    "result": None,
                    "error": "No 'entry' (old text) provided for 'update' action.",
                }
            if not new_entry or not new_entry.strip():
                return {
                    "success": False,
                    "result": None,
                    "error": "No 'new_entry' provided for 'update' action.",
                }
            old = entry.strip()
            new = new_entry.strip()
            entries = _read_entries()
            if old not in entries:
                return {
                    "success": False,
                    "result": None,
                    "error": f"Entry not found: {old}",
                }
            idx = entries.index(old)
            entries[idx] = new
            _write_entries(entries)
            return {
                "success": True,
                "result": f"Updated: {old} -> {new}",
                "error": None,
            }

        if action == "remove":
            if not entry or not entry.strip():
                return {
                    "success": False,
                    "result": None,
                    "error": "No entry provided for 'remove' action.",
                }
            target = entry.strip()
            entries = _read_entries()
            if target not in entries:
                return {
                    "success": False,
                    "result": None,
                    "error": f"Entry not found: {target}",
                }
            entries.remove(target)
            _write_entries(entries)
            return {
                "success": True,
                "result": f"Removed: {target}",
                "error": None,
            }

        return {
            "success": False,
            "result": None,
            "error": f"Unknown memory action: {action!r}",
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("memory_manage failed")
        return {"success": False, "result": None, "error": str(exc)}


def think(thought: str) -> dict:
    """Think tool — a reasoning scratchpad.

    The thought is echoed back to the model so it can reason step-by-step
    before acting.  The content is never spoken to the user.
    """
    return {
        "success": True,
        "result": thought or "",
        "error": None,
    }