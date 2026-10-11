"""JARVIS PC agent — lets the cloud JARVIS control this computer.

    jarvis-agent --setup        # one time: asks for your JARVIS address and password
    jarvis-agent                # run now (it also starts by itself when you log in)
    jarvis-agent --uninstall    # remove auto-start

The agent has no AI in it. It only connects out to your JARVIS server and
performs the few actions the server asks for (open apps and websites, type,
click, scroll, volume, screenshots). Move the mouse into a screen corner to
stop an action in progress.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.agent.client import AgentClient, AuthError

logger = logging.getLogger("jarvis.agent")

DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", "~/.jarvis")).expanduser()
CONFIG_PATH = DATA_DIR / "agent.json"
SYSTEM = platform.system()


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(config: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)  # holds the password
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Opening the JARVIS window
# --------------------------------------------------------------------------- #
def _browser_candidates() -> List[str]:
    if SYSTEM == "Windows":
        roots = [os.environ.get(v, "") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        rel = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"]
        return [str(Path(root) / r) for r in rel for root in roots if root]
    if SYSTEM == "Darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    return [p for p in (shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")) if p]


def open_ui(url: str) -> None:
    """Open JARVIS as an app window with its own browser profile.

    A separate profile means the flags below always apply (they are ignored
    when Chrome is already running) and the microphone permission and login
    are remembered.
    """
    for exe in _browser_candidates():
        if os.path.exists(exe):
            profile = DATA_DIR / "browser-profile"
            subprocess.Popen(
                [exe, f"--app={url}", f"--user-data-dir={profile}",
                 "--autoplay-policy=no-user-gesture-required", "--no-first-run",
                 "--no-default-browser-check"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
    webbrowser.open(url)


# --------------------------------------------------------------------------- #
# Auto-start at login
# --------------------------------------------------------------------------- #
def _pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def startup_path() -> Path:
    if SYSTEM == "Windows":
        appdata = Path(os.environ.get("APPDATA", "~")).expanduser()
        return appdata / "Microsoft/Windows/Start Menu/Programs/Startup/JARVIS Agent.vbs"
    if SYSTEM == "Darwin":
        return Path("~/Library/LaunchAgents/ai.jarvis.agent.plist").expanduser()
    return Path("~/.config/autostart/jarvis-agent.desktop").expanduser()


def install_startup() -> Path:
    path = startup_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if SYSTEM == "Windows":
        command = f'""{_pythonw()}"" -m jarvis.agent'
        path.write_text(f'CreateObject("Wscript.Shell").Run "{command}", 0, False\n', encoding="utf-8")
    elif SYSTEM == "Darwin":
        plist = {
            "Label": "ai.jarvis.agent",
            "ProgramArguments": [sys.executable, "-m", "jarvis.agent"],
            "RunAtLoad": True,
            "KeepAlive": True,
        }
        path.write_bytes(plistlib.dumps(plist))
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(path)], capture_output=True)
    else:
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=JARVIS Agent\n"
            f"Exec={sys.executable} -m jarvis.agent\nX-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )
    return path


def uninstall_startup() -> bool:
    path = startup_path()
    if not path.exists():
        return False
    if SYSTEM == "Darwin":
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    path.unlink()
    return True


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def setup() -> int:
    print("JARVIS PC agent setup\n")
    current = load_config()
    url = input(f"JARVIS address [{current.get('url', 'https://your-jarvis.onrender.com')}]: ").strip() or current.get("url", "")
    if not url:
        print("An address is required.")
        return 1
    token = getpass.getpass("JARVIS password (hidden): ").strip() or current.get("token", "")
    if not token:
        print("A password is required.")
        return 1
    answer = input("Open the JARVIS window automatically when this starts? [Y/n]: ").strip().lower()
    save_config({"url": url, "token": token, "open_ui": answer != "n"})
    path = install_startup()
    print(f"\nSaved. JARVIS will start automatically when you log in ({path}).")
    print("Starting it now…  (stop it any time with Ctrl+C)\n")
    return run()


def run() -> int:
    config = load_config()
    url, token = config.get("url"), config.get("token")
    if not url or not token:
        print("Not set up yet. Run:  jarvis-agent --setup")
        return 1
    if sys.stderr is None or sys.stdout is None:
        # Started without a console (pythonw at login): log to a file instead.
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(DATA_DIR / "agent.log", "a", encoding="utf-8", buffering=1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    on_connected = (lambda: open_ui(url)) if config.get("open_ui", True) else None
    client = AgentClient(url, token, on_connected=on_connected)
    try:
        asyncio.run(client.run_forever())
    except AuthError:
        print("The server refused the password. Run:  jarvis-agent --setup")
        return 2
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-agent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--setup", action="store_true", help="enter address + password, enable auto-start, run")
    parser.add_argument("--uninstall", action="store_true", help="remove auto-start")
    args = parser.parse_args(argv)
    if args.uninstall:
        print("Auto-start removed." if uninstall_startup() else "Auto-start was not installed.")
        return 0
    if args.setup:
        return setup()
    return run()


if __name__ == "__main__":
    sys.exit(main())
