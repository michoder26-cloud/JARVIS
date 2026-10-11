"""Actions the JARVIS PC agent can perform on the computer it runs on.

This module is deliberately self-contained (it imports nothing else from
``jarvis``) so the agent stays small. The cloud brain can only call the
functions in :data:`ACTIONS`; there is no shell access, no file access, and
shutdown / restart / sleep are intentionally not offered.

Every action returns a dict with at least ``{"success": bool}``.
"""

from __future__ import annotations

import base64
import io
import os
import platform
import re
import shutil
import subprocess
import time
import webbrowser
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote_plus, urlparse

SYSTEM = platform.system()  # "Windows", "Darwin" or "Linux"
IS_WINDOWS = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"

# Screenshots are downscaled to this width before being sent to the cloud.
MAX_SHOT_WIDTH = 1280
JPEG_QUALITY = 70

_ag = None
_ag_error: Optional[str] = None


def _pyautogui():
    """Return pyautogui, or ``None`` when there is no display."""
    global _ag, _ag_error
    if _ag is not None:
        return _ag
    if _ag_error is not None:
        return None
    try:
        import pyautogui  # type: ignore

        pyautogui.PAUSE = 0.15
        # Slamming the mouse into a screen corner aborts the current action:
        # a built-in emergency stop for the user.
        pyautogui.FAILSAFE = True
        _ag = pyautogui
        return _ag
    except Exception as exc:  # noqa: BLE001
        _ag_error = str(exc)
        return None


def _need_display() -> Dict[str, Any]:
    return {"success": False, "error": f"No usable display for desktop control: {_ag_error}"}


def _guarded(fn: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
    """Turn exceptions (including the pyautogui fail-safe) into error dicts."""

    def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if name == "FailSafeException":
                return {"success": False, "error": "Stopped: the mouse was moved to a screen corner."}
            return {"success": False, "error": f"{name}: {exc}"}

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# --------------------------------------------------------------------------- #
# Opening things
# --------------------------------------------------------------------------- #
_WINDOWS_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "paint": "mspaint.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "snipping tool": "snippingtool.exe",
    "settings": "ms-settings:",
}


_HOST_RE = re.compile(r"^(localhost|([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,})$", re.IGNORECASE)


def _normalize_url(url: str) -> Optional[str]:
    """Return a safe http(s) URL, or ``None`` (a bare 'youtube.com' gets https://)."""
    url = (url or "").strip()
    if not url or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    try:
        host = parsed.hostname
        parsed.port  # raises ValueError on a bad port
    except ValueError:
        return None
    if not host or not _HOST_RE.match(host):
        return None
    return url


@_guarded
def open_url(url: str) -> Dict[str, Any]:
    """Open a web page in the default browser (http/https only)."""
    target = _normalize_url(url)
    if target is None:
        return {"success": False, "error": "Only http(s) web addresses can be opened."}
    webbrowser.open(target)
    return {"success": True, "url": target}


@_guarded
def search_web(query: str, site: str = "google") -> Dict[str, Any]:
    """Open a Google or YouTube search in the default browser."""
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "No search query."}
    if site.lower() == "youtube":
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    else:
        url = f"https://www.google.com/search?q={quote_plus(query)}"
    webbrowser.open(url)
    return {"success": True, "url": url}


@_guarded
def open_app(app_name: str) -> Dict[str, Any]:
    """Open an installed application by name."""
    name = (app_name or "").strip()
    if not name:
        return {"success": False, "error": "No application name."}
    low = name.lower()

    if IS_MAC:
        done = subprocess.run(["open", "-a", name], capture_output=True, text=True)
        if done.returncode == 0:
            return {"success": True, "app": name, "method": "open -a"}
        return {"success": False, "error": done.stderr.strip() or f"Could not find '{name}'."}

    if IS_WINDOWS:
        if low in _WINDOWS_APPS:
            os.startfile(_WINDOWS_APPS[low])  # type: ignore[attr-defined]
            return {"success": True, "app": name, "method": "startfile"}
        # Same as a person would: Start menu search finds any installed app.
        ag = _pyautogui()
        if ag is None:
            return _need_display()
        ag.press("win")
        time.sleep(0.7)
        _type(name)
        time.sleep(1.0)
        ag.press("enter")
        return {"success": True, "app": name, "method": "start-menu search"}

    exe = shutil.which(low) or shutil.which(name)
    if exe:
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"success": True, "app": name, "method": "exec"}
    return {"success": False, "error": f"'{name}' was not found on this computer."}


# --------------------------------------------------------------------------- #
# Keyboard and mouse
# --------------------------------------------------------------------------- #
_KEY_ALIASES = {
    "return": "enter", "esc": "escape", "del": "delete", "page_up": "pageup",
    "page_down": "pagedown", "control": "ctrl", "windows": "win", "super": "win",
    "command": "command", "cmd": "command", "option": "alt", "spacebar": "space",
    "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute",
    "play": "playpause", "pause": "playpause", "play_pause": "playpause",
    "next": "nexttrack", "previous": "prevtrack",
}


def _type(text: str) -> None:
    """Type *text*; non-ASCII text (Thai, German umlauts...) goes via paste."""
    ag = _pyautogui()
    if text.isascii():
        ag.write(text, interval=0.02)
        return
    import pyperclip  # type: ignore

    old = None
    try:
        old = pyperclip.paste()
    except Exception:  # noqa: BLE001
        pass
    pyperclip.copy(text)
    time.sleep(0.1)
    ag.hotkey("command" if IS_MAC else "ctrl", "v")
    time.sleep(0.3)
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:  # noqa: BLE001
            pass


@_guarded
def type_text(text: str) -> Dict[str, Any]:
    """Type text at the current cursor position."""
    if not text:
        return {"success": False, "error": "No text."}
    if _pyautogui() is None:
        return _need_display()
    _type(text)
    return {"success": True, "typed": len(text)}


@_guarded
def press_key(key: str) -> Dict[str, Any]:
    """Press a key or a combination such as ``ctrl+c`` or ``alt+tab``."""
    ag = _pyautogui()
    if ag is None:
        return _need_display()
    parts = [p.strip().lower() for p in (key or "").split("+") if p.strip()]
    if not parts:
        return {"success": False, "error": "No key."}
    keys = [_KEY_ALIASES.get(p, p) for p in parts]
    bad = [k for k in keys if k not in ag.KEYBOARD_KEYS]
    if bad:
        return {"success": False, "error": f"Unknown key(s): {', '.join(bad)}"}
    if len(keys) == 1:
        ag.press(keys[0])
    else:
        ag.hotkey(*keys)
    return {"success": True, "key": "+".join(keys)}


@_guarded
def click(x: int, y: int, clicks: int = 1, button: str = "left") -> Dict[str, Any]:
    """Click at screen coordinates (in the real screen's pixels)."""
    ag = _pyautogui()
    if ag is None:
        return _need_display()
    width, height = ag.size()
    x, y = int(x), int(y)
    # Keep clear of the corners: moving there triggers the emergency stop.
    if not (5 <= x < width - 5 and 5 <= y < height - 5):
        return {"success": False, "error": f"({x},{y}) is outside the usable screen {width}x{height}."}
    ag.click(x, y, clicks=max(1, min(int(clicks), 3)), button="right" if button == "right" else "left")
    return {"success": True, "x": x, "y": y}


@_guarded
def scroll(direction: str = "down", clicks: int = 5) -> Dict[str, Any]:
    """Scroll the window under the mouse."""
    ag = _pyautogui()
    if ag is None:
        return _need_display()
    amount = max(1, min(int(clicks), 30)) * (120 if IS_WINDOWS else 3)
    ag.scroll(-amount if direction == "down" else amount)
    return {"success": True, "direction": direction}


@_guarded
def switch_window() -> Dict[str, Any]:
    """Switch to the next window (Alt+Tab / Cmd+Tab)."""
    return press_key("command+tab" if IS_MAC else "alt+tab")


@_guarded
def close_window() -> Dict[str, Any]:
    """Close the current window (Alt+F4 / Cmd+W)."""
    return press_key("command+w" if IS_MAC else "alt+f4")


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
@_guarded
def system_command(command: str) -> Dict[str, Any]:
    """Volume and screen-lock control. Shutdown/restart/sleep are not offered."""
    command = (command or "").strip().lower()
    if command in ("volume_up", "volume_down", "mute"):
        if IS_MAC:
            script = {
                "volume_up": "set volume output volume ((output volume of (get volume settings)) + 10)",
                "volume_down": "set volume output volume ((output volume of (get volume settings)) - 10)",
                "mute": "set volume output muted not (output muted of (get volume settings))",
            }[command]
            subprocess.run(["osascript", "-e", script], check=False)
            return {"success": True, "command": command}
        return press_key({"volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute"}[command])
    if command == "lock":
        if IS_WINDOWS:
            subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
        elif IS_MAC:
            subprocess.Popen(["pmset", "displaysleepnow"])
        else:
            for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"]):
                if shutil.which(cmd[0]):
                    subprocess.Popen(cmd)
                    break
        return {"success": True, "command": "lock"}
    return {"success": False, "error": f"'{command}' is not available (allowed: volume_up, volume_down, mute, lock)."}


@_guarded
def screenshot() -> Dict[str, Any]:
    """Capture the screen as a downscaled JPEG (base64) for the cloud's vision model."""
    ag = _pyautogui()
    if ag is None:
        return _need_display()
    image = ag.screenshot().convert("RGB")
    width, height = image.size
    if width > MAX_SHOT_WIDTH:
        image = image.resize((MAX_SHOT_WIDTH, round(height * MAX_SHOT_WIDTH / width)))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return {
        "success": True,
        "image_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "image_width": image.size[0],
        "image_height": image.size[1],
        "screen_width": width,
        "screen_height": height,
    }


def screen_size() -> Optional[Tuple[int, int]]:
    ag = _pyautogui()
    if ag is None:
        return None
    try:
        size = ag.size()
        return int(size[0]), int(size[1])
    except Exception:  # noqa: BLE001
        return None


ACTIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "open_url": open_url,
    "search_web": search_web,
    "open_app": open_app,
    "type_text": type_text,
    "press_key": press_key,
    "click": click,
    "scroll": scroll,
    "switch_window": switch_window,
    "close_window": close_window,
    "system_command": system_command,
    "screenshot": screenshot,
}


def run_action(name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run an allowed action; never raises."""
    fn = ACTIONS.get(name)
    if fn is None:
        return {"success": False, "error": f"Action '{name}' is not allowed on this computer."}
    try:
        return fn(**(args if isinstance(args, dict) else {}))
    except TypeError as exc:
        return {"success": False, "error": f"Invalid arguments for '{name}': {exc}"}
