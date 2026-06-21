"""Smart computer control actions — vision-guided, like a human user.

These actions combine :mod:`jarvis.vision.eyes` (ScreenVision) with
:mod:`jarvis.actions.desktop` (pyautogui) so JARVIS can *see* the screen
and interact with UI elements by visual description rather than hard-coded
coordinates.

The ScreenVision import is done **lazily inside each function** so this
module can always be imported even if the vision subsystem (PIL, OpenCV,
multimodal model, …) isn't installed yet.  When vision is unavailable the
actions return a graceful error dict instead of raising.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional

from jarvis.actions import desktop as _desktop

logger = logging.getLogger(__name__)

__all__ = [
    "smart_click",
    "smart_type",
    "smart_navigate",
    "open_website",
    "switch_window",
    "close_window",
    "scroll",
    "verify_screen",
]


# --------------------------------------------------------------------------- #
# Lazy ScreenVision loader
# --------------------------------------------------------------------------- #
_ScreenVision = None  # cached class once imported
_ScreenVision_error: Optional[str] = None


def _get_screen_vision():
    """Return an instantiated ScreenVision, or ``None`` if unavailable.

    The import is deferred to the first call so a missing/unfinished
    ``jarvis.vision.eyes`` module never breaks importing this file.
    """
    global _ScreenVision, _ScreenVision_error
    if _ScreenVision is not None:
        return _ScreenVision
    if _ScreenVision_error is not None:
        return None
    try:
        from jarvis.vision.eyes import ScreenVision  # type: ignore

        _ScreenVision = ScreenVision()
        return _ScreenVision
    except Exception as exc:  # noqa: BLE001
        _ScreenVision_error = str(exc)
        logger.warning("ScreenVision unavailable: %s", exc)
        return None


def _vision_unavailable_error() -> dict:
    return {
        "success": False,
        "error": (
            "Screen vision is unavailable — jarvis.vision.eyes could not "
            f"be imported. Underlying error: {_ScreenVision_error}"
        ),
    }


# --------------------------------------------------------------------------- #
# URL detection helper
# --------------------------------------------------------------------------- #
_URL_RE = re.compile(
    r"\b([a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|th|de|uk|info|biz|tv|me|app|dev))\b",
    re.IGNORECASE,
)


def _looks_like_url(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if text.startswith(("http://", "https://", "www.")):
        return True
    return bool(_URL_RE.search(text))


def _normalise_url(url: str) -> str:
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("www."):
        return "https://" + url
    return "https://" + url


# --------------------------------------------------------------------------- #
# Smart actions
# --------------------------------------------------------------------------- #
def smart_click(description: str) -> dict:
    """Find and click an element by visual description.

    Uses ScreenVision to locate the element on screen, then pyautogui to
    click at its coordinates.

    Example
    -------
    >>> smart_click("the YouTube search button")
    {"success": True, "x": 980, "y": 120, "description": "the YouTube search button"}
    """
    if not description:
        return {"success": False, "error": "No element description provided."}

    vision = _get_screen_vision()
    if vision is None:
        return _vision_unavailable_error()

    try:
        result = vision.find_element(description)
        if not isinstance(result, dict) or not result.get("found"):
            return {
                "success": False,
                "error": f"Could not find element matching '{description}'.",
                "description": description,
                "vision_result": result,
            }
        x = int(result.get("x"))
        y = int(result.get("y"))
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"Vision lookup failed: {exc}",
                "description": description}

    click_res = _desktop.click_coordinates(x, y)
    if not click_res.get("success"):
        return {**click_res, "description": description}

    return {
        "success": True,
        "x": x,
        "y": y,
        "description": description,
    }


def smart_type(field_description: str, text: str) -> dict:
    """Find a text field by description, click it, and type *text*.

    1. Find the field using vision.
    2. Click it.
    3. Clear any existing text (Ctrl+A then Delete).
    4. Type the new text with pyautogui.
    """
    if not field_description:
        return {"success": False, "error": "No field description provided."}
    if text is None:
        return {"success": False, "error": "No text provided."}

    ag = _desktop._get_pyautogui()
    if ag is None:
        return _desktop._no_display_error()

    # 1 & 2 — find and click the field
    click_res = smart_click(field_description)
    if not click_res.get("success"):
        return {**click_res, "field": field_description, "text": text}

    try:
        # 3 — clear existing text
        time.sleep(0.15)
        ag.hotkey("ctrl", "a")
        time.sleep(0.05)
        ag.press("delete")
        time.sleep(0.1)
        # 4 — type new text
        ag.write(text)
        return {
            "success": True,
            "field": field_description,
            "text": text,
            "x": click_res.get("x"),
            "y": click_res.get("y"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc),
                "field": field_description, "text": text}


def smart_navigate(url_or_search: str) -> dict:
    """Smart browser navigation — type in the address bar or search Google.

    If *url_or_search* looks like a URL (contains a TLD like .com, .org)
    it is typed into the browser's address bar. Otherwise JARVIS opens
    Google and searches for the term.

    Uses vision to locate the address bar or Google's search box.
    """
    if not url_or_search:
        return {"success": False, "error": "No URL or search term provided."}

    vision = _get_screen_vision()
    if vision is None:
        return _vision_unavailable_error()

    ag = _desktop._get_pyautogui()
    if ag is None:
        return _desktop._no_display_error()

    try:
        # 1 — check if a browser is visible
        browser_check = vision.verify_action("A web browser is open and visible")
        if not (isinstance(browser_check, dict) and browser_check.get("verified")):
            # Open one using the existing browser action.
            from jarvis.actions import browser as _browser
            _browser.open_browser("https://www.google.com")
            time.sleep(2)

        if _looks_like_url(url_or_search):
            # 2a — find the address bar and type the URL
            target = "the browser address bar"
            click = smart_click(target)
            if not click.get("success"):
                return {**click, "input": url_or_search}
            time.sleep(0.2)
            ag.hotkey("ctrl", "a")
            ag.press("delete")
            ag.write(_normalise_url(url_or_search))
            ag.press("enter")
            return {"success": True, "kind": "url", "input": url_or_search,
                    "url": _normalise_url(url_or_search)}
        else:
            # 2b — Google search
            # Make sure we're on Google
            from jarvis.actions import browser as _browser
            _browser.navigate_to("https://www.google.com")
            time.sleep(1.5)
            type_res = smart_type("the Google search box", url_or_search)
            if not type_res.get("success"):
                return {**type_res, "input": url_or_search}
            ag.press("enter")
            return {"success": True, "kind": "search", "input": url_or_search}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc),
                "input": url_or_search}


def open_website(url: str) -> dict:
    """Open a website by controlling the browser like a human.

    1. Check if Chrome is already open (screenshot + vision).
    2. If not, open Chrome.
    3. Find the address bar using vision.
    4. Click it, type the URL, press Enter.
    5. Wait for the page to load.
    6. Verify the page loaded (screenshot + vision).
    """
    if not url:
        return {"success": False, "error": "No URL provided."}

    vision = _get_screen_vision()
    if vision is None:
        return _vision_unavailable_error()

    ag = _desktop._get_pyautogui()
    if ag is None:
        return _desktop._no_display_error()

    full_url = _normalise_url(url)
    domain = url.replace("https://", "").replace("http://", "").split("/")[0]

    try:
        # 1 — is a browser already visible?
        check = vision.verify_action("A web browser window is open and visible")
        browser_open = isinstance(check, dict) and check.get("verified")

        if not browser_open:
            # 2 — open the browser
            from jarvis.actions import browser as _browser
            _browser.open_browser("https://www.google.com")
            time.sleep(2.5)

        # 3 & 4 — find address bar, type URL
        click = smart_click("the browser address bar")
        if not click.get("success"):
            # Fallback: use Ctrl+L which focuses the address bar in most browsers.
            ag.hotkey("ctrl", "l")
            time.sleep(0.3)
        else:
            time.sleep(0.2)

        ag.hotkey("ctrl", "a")
        ag.press("delete")
        ag.write(full_url)
        ag.press("enter")

        # 5 — wait for page to load
        time.sleep(3)

        # 6 — verify
        verify = vision.verify_action(f"The website {domain} is loaded and visible")
        verified = isinstance(verify, dict) and verify.get("verified")
        return {
            "success": True,
            "url": full_url,
            "verified": verified,
            "verify_detail": verify.get("description") if isinstance(verify, dict) else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc), "url": full_url}


def switch_window() -> dict:
    """Switch to the next window (Alt+Tab)."""
    ag = _desktop._get_pyautogui()
    if ag is None:
        return _desktop._no_display_error()
    try:
        ag.hotkey("alt", "tab")
        return {"success": True, "action": "switch_window"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def close_window() -> dict:
    """Close the current window (Alt+F4)."""
    ag = _desktop._get_pyautogui()
    if ag is None:
        return _desktop._no_display_error()
    try:
        ag.hotkey("alt", "f4")
        return {"success": True, "action": "close_window"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def scroll(direction: str = "down", clicks: int = 3) -> dict:
    """Scroll the screen up or down.

    Parameters
    ----------
    direction : "up" | "down"
    clicks : int
        Number of scroll increments (default 3).
    """
    ag = _desktop._get_pyautogui()
    if ag is None:
        return _desktop._no_display_error()
    direction = (direction or "down").strip().lower()
    if direction not in ("up", "down"):
        return {"success": False, "error": f"Invalid direction '{direction}'. Use 'up' or 'down'."}
    try:
        amount = int(clicks) if direction == "down" else -int(clicks)
        ag.scroll(amount)
        return {"success": True, "direction": direction, "clicks": int(clicks)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def verify_screen(description: str) -> dict:
    """Take a screenshot and verify whether *description* is visible.

    Example
    -------
    >>> verify_screen("YouTube is showing search results")
    {"verified": True, "description": "Yes, YouTube search results are visible"}
    """
    if not description:
        return {"success": False, "error": "No description provided."}
    vision = _get_screen_vision()
    if vision is None:
        return _vision_unavailable_error()
    try:
        result = vision.verify_action(description)
        if not isinstance(result, dict):
            return {"success": False, "verified": False,
                    "error": "Vision returned unexpected type",
                    "description": description}
        return {
            "success": True,
            "verified": bool(result.get("verified")),
            "description": result.get("description", description),
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "verified": False, "error": str(exc),
                "description": description}