"""Action registry — maps LLM tool names to handler functions.

The brain / LLM layer produces a tool name (string) plus an arguments dict;
:func:`execute_action` looks up the handler and calls it, always returning a
result dict and never raising.
"""

from __future__ import annotations

from typing import Callable, Dict

from jarvis.actions.browser import (
    open_browser,
    search_youtube,
    navigate_to,
    close_browser,
    take_browser_screenshot,
)
from jarvis.actions.desktop import (
    open_app,
    type_text,
    press_key,
    click_coordinates,
    screenshot,
    get_screen_size,
)
from jarvis.actions.system import (
    system_command,
    get_time,
    get_weather,
)
from jarvis.actions.memory import (
    memory_manage,
    think,
)
from jarvis.actions.search import (
    search_web_api,
)
from jarvis.actions.news import (
    get_news,
)
from jarvis.actions.smart import (
    smart_click,
    smart_type,
    open_website,
    scroll,
    switch_window,
    close_window,
    verify_screen,
    smart_navigate,
)

# Type alias for an action handler.
ActionHandler = Callable[..., Dict]

ACTION_REGISTRY: Dict[str, ActionHandler] = {
    # Browser
    "open_browser": open_browser,
    "search_youtube": search_youtube,
    "search_web": search_web_api,
    "navigate_to": navigate_to,
    "close_browser": close_browser,
    "take_browser_screenshot": take_browser_screenshot,
    # Desktop
    "open_app": open_app,
    "type_text": type_text,
    "press_key": press_key,
    "click_coordinates": click_coordinates,
    "screenshot": screenshot,
    "get_screen_size": get_screen_size,
    # System
    "system_command": system_command,
    "get_time": get_time,
    "get_weather": get_weather,
    # News
    "get_news": get_news,
    # Memory & reasoning
    "memory_manage": memory_manage,
    "think": think,
    # Smart / vision-guided actions
    "smart_click": smart_click,
    "smart_type": smart_type,
    "open_website": open_website,
    "scroll": scroll,
    "switch_window": switch_window,
    "close_window": close_window,
    "verify_screen": verify_screen,
    "smart_navigate": smart_navigate,
}


def execute_action(name: str, args: dict | None = None) -> dict:
    """Execute an action by name with arguments.

    Parameters
    ----------
    name:
        The registered action/tool name.
    args:
        Keyword arguments forwarded to the handler.  ``None`` is treated as
        an empty dict.

    Returns
    -------
    dict
        Always returns a dict with at least a ``success`` key.  Unknown actions
        and handler exceptions are converted to error dicts.
    """
    handler = ACTION_REGISTRY.get(name)
    if handler is None:
        return {"success": False, "error": f"Unknown action: {name}"}
    if args is None:
        args = {}
    try:
        return handler(**args)
    except TypeError as exc:
        # Argument mismatch — give a clearer message than a raw TypeError.
        return {
            "success": False,
            "error": f"Invalid arguments for action '{name}': {exc}",
        }
    except Exception as exc:  # noqa: BLE001 — must never raise
        return {"success": False, "error": str(exc)}