"""Desktop automation using pyautogui (+ pywinauto on Windows).

All functions degrade gracefully: on a headless Linux machine (no DISPLAY)
pyautogui cannot initialise, so each helper detects that situation and
returns an error dict instead of crashing.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from typing import Optional

IS_WINDOWS = platform.system() == "Windows"

__all__ = [
    "open_app",
    "type_text",
    "press_key",
    "click_coordinates",
    "screenshot",
    "get_screen_size",
]

# ---------------------------------------------------------------------- #
# Lazy pyautogui import with headless detection
# ---------------------------------------------------------------------- #
_pyautogui = None
_pyautogui_error: Optional[str] = None


def _get_pyautogui():
    """Return the pyautogui module, or ``None`` if no display is available."""
    global _pyautogui, _pyautogui_error
    if _pyautogui is not None:
        return _pyautogui
    if _pyautogui_error is not None:
        # Already failed once — don't retry every call.
        return None
    try:
        import pyautogui  # type: ignore

        pyautogui.PAUSE = 0.5  # small delay between actions for reliability
        pyautogui.FAILSAFE = True  # move mouse to corner to abort
        _pyautogui = pyautogui
        return _pyautogui
    except Exception as exc:  # noqa: BLE001 — KeyError on DISPLAY, import err, ...
        _pyautogui_error = str(exc)
        return None


def _no_display_error() -> dict:
    return {
        "success": False,
        "error": (
            "Desktop automation unavailable: no graphical display detected "
            "(set DISPLAY env var or run on a machine with a desktop). "
            f"Underlying error: {_pyautogui_error}"
        ),
    }


# ---------------------------------------------------------------------- #
# App launching
# ---------------------------------------------------------------------- #
_APP_MAP_WINDOWS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "paint": "mspaint.exe",
    "snipping tool": "snippingtool.exe",
    "control panel": "control.exe",
    "settings": "start ms-settings:",  # Win10/11 settings
    "wordpad": "write.exe",
    "registry editor": "regedit.exe",
}

_APP_MAP_LINUX = {
    "terminal": "x-terminal-emulator",
    "files": "nautilus",
    "file manager": "nautilus",
    "text editor": "gedit",
    "calculator": "gnome-calculator",
    "calc": "gnome-calculator",
    "settings": "gnome-control-center",
    "browser": "xdg-open https://www.google.com",
}


def open_app(app_name: str) -> dict:
    """Open a desktop application by friendly name.

    On Windows we first try a known-name map, then fall back to ``os.startfile``
    and finally to pywinauto's ``Application().start``.  On Linux we use
    ``subprocess.Popen`` with the mapped command.
    """
    app_name = (app_name or "").strip().lower()
    if not app_name:
        return {"success": False, "error": "No application name provided."}

    try:
        if IS_WINDOWS:
            exe = _APP_MAP_WINDOWS.get(app_name)
            if exe:
                # ``start`` commands must go through the shell.
                if exe.startswith("start "):
                    subprocess.Popen(exe, shell=True)
                else:
                    try:
                        os.startfile(exe)  # type: ignore[attr-defined]
                    except Exception:
                        subprocess.Popen([exe])
                return {"success": True, "app": app_name, "executable": exe}

            # Try pywinauto for arbitrary app names.
            try:
                import pywinauto  # type: ignore

                pywinauto.Application(backend="uia").start(app_name)
                return {"success": True, "app": app_name, "executable": app_name}
            except Exception:
                pass

            # Last resort: try os.startfile on the raw name.
            try:
                os.startfile(app_name)  # type: ignore[attr-defined]
                return {"success": True, "app": app_name, "executable": app_name}
            except Exception as exc:
                return {
                    "success": False,
                    "app": app_name,
                    "error": f"Could not launch '{app_name}': {exc}",
                }
        else:
            # Linux / other
            cmd = _APP_MAP_LINUX.get(app_name)
            if cmd is None:
                # Heuristic: try the name directly as an executable.
                cmd = app_name
            try:
                if " " in cmd:
                    subprocess.Popen(cmd, shell=True)
                else:
                    subprocess.Popen([cmd])
                return {"success": True, "app": app_name, "executable": cmd}
            except FileNotFoundError:
                # Fallback: maybe it's a path or URL — use xdg-open.
                try:
                    subprocess.Popen(["xdg-open", app_name])
                    return {"success": True, "app": app_name, "executable": "xdg-open"}
                except Exception as exc:
                    return {
                        "success": False,
                        "app": app_name,
                        "error": f"Could not launch '{app_name}': {exc}",
                    }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "app": app_name, "error": str(exc)}


# ---------------------------------------------------------------------- #
# Keyboard / mouse via pyautogui
# ---------------------------------------------------------------------- #
# Friendly-name → pyautogui key name mapping.
_KEY_MAP = {
    "enter": "enter",
    "return": "enter",
    "esc": "escape",
    "escape": "escape",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "page_up": "pageup",
    "pagedown": "pagedown",
    "page_down": "pagedown",
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "cmd": "cmd",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
}


def type_text(text: str) -> dict:
    """Type *text* at the current cursor position."""
    if not text:
        return {"success": False, "error": "No text provided."}
    ag = _get_pyautogui()
    if ag is None:
        return _no_display_error()
    try:
        ag.write(text)
        return {"success": True, "text": text}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def press_key(key: str) -> dict:
    """Press a keyboard key.  Accepts friendly names like 'enter', 'esc'."""
    if not key:
        return {"success": False, "error": "No key provided."}
    ag = _get_pyautogui()
    if ag is None:
        return _no_display_error()
    mapped = _KEY_MAP.get(key.strip().lower(), key.strip().lower())
    try:
        ag.press(mapped)
        return {"success": True, "key": mapped}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def click_coordinates(x: int, y: int) -> dict:
    """Click at screen coordinates (x, y)."""
    ag = _get_pyautogui()
    if ag is None:
        return _no_display_error()
    try:
        ag.click(x, y)
        return {"success": True, "x": x, "y": y}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def screenshot(filename: Optional[str] = None) -> dict:
    """Take a screenshot and save it to *filename* (or a timestamped default)."""
    ag = _get_pyautogui()
    if ag is None:
        return _no_display_error()
    try:
        if not filename:
            ts = time.strftime("%Y%m%d-%H%M%S")
            filename = os.path.join(os.getcwd(), f"screenshot_{ts}.png")
        ag.screenshot(filename)
        return {"success": True, "path": filename}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def get_screen_size() -> dict:
    """Return the screen dimensions as ``{'width', 'height'}``."""
    ag = _get_pyautogui()
    if ag is None:
        return _no_display_error()
    try:
        w, h = ag.size()
        return {"success": True, "width": w, "height": h}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}