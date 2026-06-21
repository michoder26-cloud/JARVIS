"""System-level commands: volume, screenshots, lock/shutdown, time, weather.

Every function returns a dict with at least ``{"success": bool}``.  Dangerous
commands (``shutdown`` / ``restart``) never execute immediately — they return a
dict asking for confirmation so the LLM/voice layer can get user approval first.
"""

from __future__ import annotations

import datetime
import locale
import platform
import subprocess
import urllib.request
import urllib.error
from typing import Optional

IS_WINDOWS = platform.system() == "Windows"

__all__ = [
    "system_command",
    "get_time",
    "get_weather",
]


# ---------------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------------- #
def _press_special_key(key_name: str) -> dict:
    """Press a media / special key using pyautogui if available."""
    try:
        import pyautogui  # type: ignore
    except Exception:
        pass
    else:
        try:
            pyautogui.press(key_name)
            return {"success": True, "action": key_name}
        except Exception:
            pass

    # Fallback to platform-specific command-line tools.
    if not IS_WINDOWS:
        # Try amixer / pactl on Linux.
        try:
            if key_name == "volumeup":
                subprocess.run(["amixer", "-q", "set", "Master", "5%+"], check=False)
            elif key_name == "volumedown":
                subprocess.run(["amixer", "-q", "set", "Master", "5%-"], check=False)
            elif key_name == "volumemute":
                subprocess.run(["amixer", "-q", "set", "Master", "toggle"], check=False)
            return {"success": True, "action": key_name, "via": "amixer"}
        except FileNotFoundError:
            pass
    return {
        "success": False,
        "error": f"Could not perform '{key_name}' on this platform.",
    }


# ---------------------------------------------------------------------- #
# Public actions
# ---------------------------------------------------------------------- #
def system_command(command: str) -> dict:
    """Execute a system-level command by friendly name.

    Supported commands: ``volume_up``, ``volume_down``, ``mute``, ``screenshot``,
    ``lock``, ``shutdown``, ``restart``.

    ``shutdown`` and ``restart`` do **not** execute immediately — they return a
    confirmation request so the caller can ask the user first.
    """
    command = (command or "").strip().lower()
    if not command:
        return {"success": False, "error": "No command provided."}

    try:
        if command in ("volume_up", "volumeup", "vol_up"):
            return _press_special_key("volumeup")
        if command in ("volume_down", "volumedown", "vol_down"):
            return _press_special_key("volumedown")
        if command in ("mute", "unmute", "toggle_mute"):
            return _press_special_key("volumemute")
        if command == "screenshot":
            # Import lazily to avoid pulling pyautogui on headless machines
            # unless the user actually asked for a screenshot.
            from jarvis.actions.desktop import screenshot as _ss

            return _ss()
        if command == "lock":
            if IS_WINDOWS:
                subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
            else:
                # Try common Linux lockers.
                for cmd in (
                    ["gnome-screensaver-command", "-l"],
                    ["loginctl", "lock-session"],
                    ["xdg-screensaver", "lock"],
                    ["i3lock"],
                ):
                    try:
                        subprocess.Popen(cmd)
                        break
                    except FileNotFoundError:
                        continue
            return {"success": True, "action": "lock"}
        if command in ("shutdown", "poweroff"):
            # Do NOT shut down without explicit confirmation from the user.
            return {
                "success": True,
                "action": "shutdown",
                "confirm_required": True,
                "message": (
                    "This will shut down the computer. "
                    "Call again with confirm=True to proceed."
                ),
            }
        if command in ("restart", "reboot"):
            return {
                "success": True,
                "action": "restart",
                "confirm_required": True,
                "message": (
                    "This will restart the computer. "
                    "Call again with confirm=True to proceed."
                ),
            }
        return {"success": False, "error": f"Unknown system command: {command}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def get_time() -> dict:
    """Return the current time as a human-readable string in the user's locale."""
    try:
        # Try to use the system locale; fall back to default if unavailable.
        try:
            loc = locale.getdefaultlocale()[0]
            if loc:
                locale.setlocale(locale.LC_TIME, loc)
        except Exception:
            pass
        now = datetime.datetime.now()
        return {
            "success": True,
            "time": now.strftime("%I:%M %p"),
            "time_24h": now.strftime("%H:%M"),
            "date": now.strftime("%A, %B %d, %Y"),
            "iso": now.isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def get_weather(location: Optional[str] = None) -> dict:
    """Fetch weather from the free wttr.in service (no API key required).

    Returns a concise weather string plus a detailed breakdown.
    """
    loc = location or ""
    # wttr.in accepts city names, airport codes, or "~" for auto-detection.
    if not loc:
        loc = "~"
    # Sanitise for URL (replace spaces).
    loc_url = loc.strip().replace(" ", "+")

    try:
        # Simple one-line format for quick speech output.
        simple_url = f"https://wttr.in/{loc_url}?format=3"
        req = urllib.request.Request(simple_url, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            simple = resp.read().decode("utf-8", errors="replace").strip()

        # Detailed format: condition + temp + humidity + wind.
        detail_url = f"https://wttr.in/{loc_url}?format=%C+%t+%h+%w"
        req2 = urllib.request.Request(detail_url, headers={"User-Agent": "curl/8.0"})
        try:
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                detail = resp2.read().decode("utf-8", errors="replace").strip()
        except Exception:
            detail = simple

        return {
            "success": True,
            "location": loc,
            "summary": simple,
            "detail": detail,
        }
    except urllib.error.URLError as exc:
        return {"success": False, "error": f"Weather request failed: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}