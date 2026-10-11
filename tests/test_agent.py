"""Tests for the PC agent: its actions, and a real connection to a live server."""

import asyncio
import json
import socket
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from jarvis.agent import actions
from jarvis.agent.client import AgentClient, AuthError, agent_ws_url


def test_ws_url():
    assert agent_ws_url("https://x.onrender.com/") == "wss://x.onrender.com/agent"
    assert agent_ws_url("x.onrender.com") == "wss://x.onrender.com/agent"
    assert agent_ws_url("http://localhost:8000") == "ws://localhost:8000/agent"


class FakeAG:
    KEYBOARD_KEYS = ["enter", "ctrl", "alt", "tab", "c", "v", "volumeup", "f4", "win", "command"]

    def __init__(self):
        self.log = []

    def press(self, *a, **k): self.log.append(("press", a))
    def hotkey(self, *a, **k): self.log.append(("hotkey", a))
    def write(self, text, interval=0): self.log.append(("write", text))
    def click(self, x, y, clicks=1, button="left"): self.log.append(("click", x, y, clicks, button))
    def scroll(self, n): self.log.append(("scroll", n))
    def size(self): return (1920, 1080)

    def screenshot(self):
        from PIL import Image
        return Image.new("RGB", (2560, 1440), "white")


@pytest.fixture
def ag(monkeypatch):
    fake = FakeAG()
    monkeypatch.setattr(actions, "_ag", fake)
    return fake


def test_unknown_and_dangerous_actions_are_refused(ag):
    assert not actions.run_action("shutdown")["success"]
    assert not actions.run_action("run_shell", {"cmd": "rm -rf /"})["success"]
    for cmd in ("shutdown", "restart", "sleep", "poweroff"):
        assert not actions.run_action("system_command", {"command": cmd})["success"]
    assert not actions.run_action("click", {"bogus": 1})["success"]  # bad arguments don't raise


def test_only_web_addresses_can_be_opened(monkeypatch):
    opened = []
    monkeypatch.setattr(actions.webbrowser, "open", opened.append)
    assert actions.open_url("youtube.com")["url"] == "https://youtube.com"
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "ftp://x", "", "http://"):
        assert not actions.open_url(bad)["success"], bad
    assert opened == ["https://youtube.com"]
    assert "search_query=lo+fi" in actions.search_web("lo fi", "youtube")["url"]


def test_keys_and_combinations(ag):
    assert actions.press_key("ctrl+c")["success"]
    assert actions.press_key("Return")["success"]
    assert not actions.press_key("ctrl+nonsense")["success"]
    assert ag.log == [("hotkey", ("ctrl", "c")), ("press", ("enter",))]


def test_thai_text_is_pasted_not_typed(ag, monkeypatch):
    clip = []
    monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(
        paste=lambda: "old", copy=clip.append))
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    actions.type_text("hello")
    actions.type_text("สวัสดี")
    assert ("write", "hello") in ag.log
    assert clip[0] == "สวัสดี" and clip[-1] == "old"  # clipboard restored
    assert ("hotkey", ("command" if actions.IS_MAC else "ctrl", "v")) in ag.log


def test_clicks_keep_away_from_the_emergency_stop_corners(ag):
    assert actions.click(960, 540)["success"]
    assert not actions.click(0, 0)["success"]
    assert not actions.click(1919, 1079)["success"]
    assert not actions.click(5000, 5)["success"]
    assert ag.log == [("click", 960, 540, 1, "left")]


def test_screenshot_is_downscaled_and_reports_real_size(ag):
    out = actions.screenshot()
    assert out["success"] and out["image_width"] == 1280 and out["image_height"] == 720
    assert out["screen_width"] == 2560 and len(out["image_b64"]) < 200_000


def test_failsafe_becomes_an_error_result(ag, monkeypatch):
    class FailSafeException(Exception):
        pass

    def boom(*a, **k):
        raise FailSafeException("corner")

    monkeypatch.setattr(ag, "click", boom)
    out = actions.click(500, 500)
    assert not out["success"] and "corner" in out["error"]


# --------------------------------------------------------------------------- #
# Real sockets: live uvicorn server + AgentClient + a browser-like websocket
# --------------------------------------------------------------------------- #
@pytest.fixture
def live_server(tmp_path):
    import uvicorn
    from tests.test_web import FakeAI, FakeLLM
    from jarvis.sessions.store import SessionStore
    from jarvis.web import server as web

    settings = web.Settings(api_key="x", access_token="s3cret-pass")
    llm = FakeLLM()
    app = web.JarvisServer(settings, ai=FakeAI(), llm=llm, store=SessionStore(str(tmp_path / "s.db"))).build()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}", llm=llm, port=port)
    server.should_exit = True
    thread.join(5)


def test_wrong_password_stops_the_agent(live_server):
    client = AgentClient(live_server.url, "wrong", runner=lambda n, a: {"success": True})
    with pytest.raises(AuthError):
        asyncio.run(asyncio.wait_for(client.run_forever(), 20))


def test_full_chain_browser_to_cloud_to_agent(live_server):
    """A chat message makes the cloud call the agent, which runs the action."""
    import httpx
    import websockets

    live_server.llm.script = [("open_app", {"app_name": "spotify"})]
    ran = []

    def runner(name, args):
        ran.append((name, args))
        return {"success": True, "ran": name}

    async def scenario():
        agent = AgentClient(live_server.url, "s3cret-pass", runner=runner)
        agent_task = asyncio.create_task(agent.run_forever())
        login = httpx.post(f"{live_server.url}/api/auth", headers={"Authorization": "Bearer s3cret-pass"})
        cookie = login.headers["set-cookie"].split(";")[0]
        async with websockets.connect(
            f"ws://127.0.0.1:{live_server.port}/ws", additional_headers={"Cookie": cookie}
        ) as browser:
            await browser.recv()  # hello
            for _ in range(100):  # wait until the agent has connected
                if httpx.get(f"{live_server.url}/api/health").json()["agent"]["online"]:
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("agent never connected")
            await browser.send(json.dumps({"type": "chat", "text": "open spotify"}))
            while True:
                msg = json.loads(await asyncio.wait_for(browser.recv(), 15))
                if msg["type"] == "chat_reply":
                    break
        agent_task.cancel()
        return msg

    reply = asyncio.run(scenario())
    assert ran == [("open_app", {"app_name": "spotify"})]
    assert reply["tools"] == ["open_app"] and not reply["error"]
