"""Connection from the PC agent to the JARVIS cloud server.

The agent only makes an *outbound* WebSocket connection, so no router or
firewall settings are needed. It reconnects automatically, e.g. when the
free cloud server wakes up from sleep.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from jarvis.agent import actions

logger = logging.getLogger("jarvis.agent")

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 16 * 1024 * 1024


def agent_ws_url(base_url: str) -> str:
    """``https://x.onrender.com`` -> ``wss://x.onrender.com/agent``."""
    url = base_url.strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    scheme, rest = url.split("://", 1)
    ws_scheme = "wss" if scheme == "https" else "ws"
    return f"{ws_scheme}://{rest}/agent"


class AuthError(Exception):
    """The server rejected the access token."""


class AgentClient:
    """Receives action requests from the cloud and runs them locally."""

    def __init__(
        self,
        url: str,
        token: str,
        on_connected: Optional[Callable[[], None]] = None,
        runner: Callable[[str, Optional[Dict[str, Any]]], Dict[str, Any]] = actions.run_action,
    ) -> None:
        self.url = agent_ws_url(url)
        self.token = token
        self.on_connected = on_connected
        self._runner = runner
        # One action at a time: they share the mouse, keyboard and screen.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="action")
        self._connected_once = False

    def hello(self) -> Dict[str, Any]:
        size = actions.screen_size()
        return {
            "type": "hello",
            "protocol": PROTOCOL_VERSION,
            "host": socket.gethostname(),
            "os": platform.system(),
            "screen": list(size) if size else None,
            "actions": sorted(actions.ACTIONS),
        }

    async def run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                await self._session()
                delay = 1.0
            except AuthError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — network errors, server asleep, ...
                logger.info("Disconnected (%s). Retrying in %.0fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _session(self) -> None:
        import websockets

        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            connection = websockets.connect(
                self.url, additional_headers=headers, max_size=MAX_MESSAGE_BYTES,
                ping_interval=20, ping_timeout=30, open_timeout=60,
            )
        except TypeError:  # websockets < 14
            connection = websockets.connect(
                self.url, extra_headers=headers, max_size=MAX_MESSAGE_BYTES,
                ping_interval=20, ping_timeout=30, open_timeout=60,
            )
        try:
            ws = await connection
        except Exception as exc:  # noqa: BLE001
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                raise AuthError("The server refused the access token.") from exc
            raise
        async with ws:
            await ws.send(json.dumps(self.hello()))
            logger.info("Connected to %s", self.url)
            if not self._connected_once:
                self._connected_once = True
                if self.on_connected:
                    self.on_connected()
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if msg.get("type") == "call":
                    asyncio.create_task(self._handle_call(ws, msg))

    async def _handle_call(self, ws, msg: Dict[str, Any]) -> None:
        name = str(msg.get("name", ""))
        logger.info("Action: %s", name)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(self._pool, self._runner, name, msg.get("args"))
        try:
            await ws.send(json.dumps({"type": "result", "id": msg.get("id"), "result": result}))
        except Exception:  # noqa: BLE001 — connection dropped mid-action
            logger.info("Could not send result for %s", name)
