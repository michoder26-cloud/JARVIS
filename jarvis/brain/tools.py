"""Tool/function schemas for JARVIS function calling.

Each tool is defined as a JSON schema in the format that Ollama's native
tool-calling expects::

    {
        "type": "function",
        "function": {
            "name": "...",
            "description": "...",
            "parameters": { "type": "object", "properties": {...}, "required": [...] }
        }
    }

The :data:`ALL_TOOLS` list is passed directly to ``ollama.chat(tools=...)``.

A dispatch registry (:func:`get_tool_handler` / :func:`register_tool_handler`)
lets the Actions module wire real handlers without modifying this file.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Type for a tool handler: takes **kwargs, returns a dict (result).
ToolHandler = Callable[..., Dict[str, Any]]


# --------------------------------------------------------------------------- #
# Tool schema definitions
# --------------------------------------------------------------------------- #

def _tool(name: str, description: str, properties: Dict[str, Any],
          required: List[str]) -> Dict[str, Any]:
    """Helper to build a tool schema in Ollama's native format."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


OPEN_BROWSER = _tool(
    name="open_browser",
    description=(
        "Open a website in the default web browser. "
        "Optionally perform a search on that site. "
        "Use this for opening any URL the user requests."
    ),
    properties={
        "url": {
            "type": "string",
            "description": (
                "The full URL to open, e.g. 'https://google.com', "
                "'https://youtube.com'. Include the https:// prefix."
            ),
        },
        "search": {
            "type": "string",
            "description": (
                "Optional search query. If provided, perform a search on "
                "the opened site (e.g. search on YouTube or Google)."
            ),
        },
    },
    required=["url"],
)


SEARCH_YOUTUBE = _tool(
    name="search_youtube",
    description=(
        "Open YouTube and search for a query. Use this when the user wants "
        "to play a song, watch a video, or find content on YouTube."
    ),
    properties={
        "query": {
            "type": "string",
            "description": (
                "The search query, e.g. a song name, artist, or video title."
            ),
        },
    },
    required=["query"],
)


OPEN_APP = _tool(
    name="open_app",
    description=(
        "Open a desktop application by name. Examples: 'spotify', 'notepad', "
        "'calculator', 'code', 'terminal', 'firefox'."
    ),
    properties={
        "app_name": {
            "type": "string",
            "description": "The name of the application to launch.",
        },
    },
    required=["app_name"],
)


TYPE_TEXT = _tool(
    name="type_text",
    description=(
        "Type text at the current cursor position. Use this when the user "
        "dictates text to type into a field or document."
    ),
    properties={
        "text": {
            "type": "string",
            "description": "The text to type.",
        },
    },
    required=["text"],
)


CLICK_COORDINATES = _tool(
    name="click_coordinates",
    description=(
        "Click the mouse at specific screen coordinates (x, y in pixels). "
        "Use this when the user asks to click a specific spot on screen."
    ),
    properties={
        "x": {
            "type": "integer",
            "description": "The X coordinate (horizontal, from left).",
        },
        "y": {
            "type": "integer",
            "description": "The Y coordinate (vertical, from top).",
        },
    },
    required=["x", "y"],
)


PRESS_KEY = _tool(
    name="press_key",
    description=(
        "Press a keyboard key. Examples: 'enter', 'escape', 'tab', 'space', "
        "'backspace', 'ctrl+c', 'alt+tab', 'f5'."
    ),
    properties={
        "key": {
            "type": "string",
            "description": (
                "The key or key combination to press. Single keys: 'enter', "
                "'escape', 'tab', 'space'. Combinations use '+': 'ctrl+c'."
            ),
        },
    },
    required=["key"],
)


SCREENSHOT = _tool(
    name="screenshot",
    description=(
        "Take a screenshot of the current screen. Returns the file path "
        "of the saved image."
    ),
    properties={},
    required=[],
)


SYSTEM_COMMAND = _tool(
    name="system_command",
    description=(
        "Execute a system control command. Supported commands: "
        "'volume_up', 'volume_down', 'volume_mute', 'volume_unmute', "
        "'brightness_up', 'brightness_down', 'lock_screen', 'sleep', "
        "'shutdown', 'restart'."
    ),
    properties={
        "command": {
            "type": "string",
            "description": (
                "The system command to execute. One of: volume_up, "
                "volume_down, volume_mute, volume_unmute, brightness_up, "
                "brightness_down, lock_screen, sleep, shutdown, restart."
            ),
            "enum": [
                "volume_up", "volume_down", "volume_mute", "volume_unmute",
                "brightness_up", "brightness_down", "lock_screen",
                "sleep", "shutdown", "restart",
            ],
        },
    },
    required=["command"],
)


SEARCH_WEB = _tool(
    name="search_web",
    description=(
        "Search the web and return text results. Use this when the user "
        "asks a factual question or wants to look something up."
    ),
    properties={
        "query": {
            "type": "string",
            "description": "The search query.",
        },
    },
    required=["query"],
)


GET_WEATHER = _tool(
    name="get_weather",
    description=(
        "Get current weather information for a location. If no location "
        "is given, uses the user's current location."
    ),
    properties={
        "location": {
            "type": "string",
            "description": (
                "City or place name, e.g. 'Bangkok', 'Munich', 'New York'. "
                "If omitted, uses the user's current location."
            ),
        },
    },
    required=[],
)


GET_TIME = _tool(
    name="get_time",
    description=(
        "Get the current date and time. Use this when the user asks for "
        "the time or date."
    ),
    properties={},
    required=[],
)


ASK_USER = _tool(
    name="ask_user",
    description=(
        "Ask the user a clarifying question via text-to-speech and wait "
        "for their spoken answer. Use this when a command is ambiguous "
        "and you need more information to proceed."
    ),
    properties={
        "question": {
            "type": "string",
            "description": (
                "The question to ask the user. Keep it short and clear. "
                "It will be spoken aloud."
            ),
        },
    },
    required=["question"],
)


# --------------------------------------------------------------------------- #
# Aggregate list — passed to ollama.chat(tools=ALL_TOOLS)
# --------------------------------------------------------------------------- #

ALL_TOOLS: List[Dict[str, Any]] = [
    OPEN_BROWSER,
    SEARCH_YOUTUBE,
    OPEN_APP,
    TYPE_TEXT,
    CLICK_COORDINATES,
    PRESS_KEY,
    SCREENSHOT,
    SYSTEM_COMMAND,
    SEARCH_WEB,
    GET_WEATHER,
    GET_TIME,
    ASK_USER,
]

# Quick name → schema lookup.
TOOL_NAMES: Dict[str, Dict[str, Any]] = {
    t["function"]["name"]: t for t in ALL_TOOLS
}


# --------------------------------------------------------------------------- #
# Handler registry — the Actions module registers real implementations here.
# --------------------------------------------------------------------------- #

# Maps tool name → handler function.
_HANDLERS: Dict[str, ToolHandler] = {}


def register_tool_handler(name: str, handler: ToolHandler) -> None:
    """Register a handler for a tool name.

    Called by the Actions module to connect real implementations::

        from jarvis.brain.tools import register_tool_handler
        register_tool_handler("open_browser", my_open_browser)
    """
    if name not in TOOL_NAMES:
        logger.warning("Registering handler for unknown tool %r", name)
    _HANDLERS[name] = handler
    logger.debug("Registered handler for tool %r", name)


def register_handlers(mapping: Dict[str, ToolHandler]) -> None:
    """Register multiple tool handlers at once."""
    for name, handler in mapping.items():
        register_tool_handler(name, handler)


def get_tool_handler(name: str) -> Optional[ToolHandler]:
    """Return the handler for *name*, or None if not registered."""
    return _HANDLERS.get(name)


def dispatch_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool by name with the given arguments.

    Returns a result dict::

        {"success": bool, "result": Any, "error": str | None}

    If no handler is registered, returns an error indicating the action
    module is not connected yet.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        msg = (f"No handler registered for tool '{name}'. "
               f"The action module may not be connected.")
        logger.warning(msg)
        return {"success": False, "result": None, "error": msg}

    try:
        result = handler(**arguments)
        # Ensure result is a dict.
        if not isinstance(result, dict):
            result = {"success": True, "result": result, "error": None}
        return result
    except Exception as exc:
        logger.exception("Tool %r raised an exception", name)
        return {"success": False, "result": None, "error": str(exc)}


def clear_handlers() -> None:
    """Remove all registered handlers (mainly for testing)."""
    _HANDLERS.clear()