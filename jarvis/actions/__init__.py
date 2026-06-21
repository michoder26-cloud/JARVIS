"""JARVIS Actions module — browser, desktop, and system automation.

Exports every public action function plus the :data:`ACTION_REGISTRY` mapping
and the :func:`execute_action` dispatcher.
"""

from __future__ import annotations

from jarvis.actions.browser import (
    open_browser,
    search_youtube,
    search_web,
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
from jarvis.actions.registry import (
    ACTION_REGISTRY,
    execute_action,
)

__all__ = [
    # Browser
    "open_browser",
    "search_youtube",
    "search_web",
    "navigate_to",
    "close_browser",
    "take_browser_screenshot",
    # Desktop
    "open_app",
    "type_text",
    "press_key",
    "click_coordinates",
    "screenshot",
    "get_screen_size",
    # System
    "system_command",
    "get_time",
    "get_weather",
    # Registry
    "ACTION_REGISTRY",
    "execute_action",
]