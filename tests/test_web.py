"""Tests for the cloud web server, using fake AI services."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from jarvis.sessions.store import SessionStore
from jarvis.web import server as web
from jarvis.web.ai import _chunks, language_name


class FakeAI:
    configured = True

    def __init__(self):
        self.calls = []

    async def _gen(self, *parts):
        for p in parts:
            await asyncio.sleep(0)
            yield p

    def translate(self, text, target, context=()):
        self.calls.append(("translate", text, target, list(context)))
        return self._gen(f"[{target}] ", text)

    def update_notes(self, notes, new_transcript, lang):
        self.calls.append(("notes", notes, new_transcript, lang))
        return self._gen("## Topic\n", "- point")

    def summarize(self, transcript, lang):
        self.calls.append(("summary", transcript, lang))
        return self._gen("# Title\n", "summary")

    def answer(self, question, transcript, notes, lang):
        self.calls.append(("answer", question))
        return self._gen("answer to ", question)


class FakeVisionClient:
    """Stands in for the OpenAI client when analysing screenshots."""

    def __init__(self, reply):
        self.reply = reply
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        from types import SimpleNamespace as NS
        return NS(choices=[NS(message=NS(content=self.reply))])


class FakeLLM:
    def __init__(self):
        self.messages = None
        self.tools = None
        self.script = []  # [(tool name, args)] to call instead of get_time
        self.vision_reply = '{"found": true, "x": 50, "y": 25}'

    def _ensure_client(self):
        return FakeVisionClient(self.vision_reply)

    def chat_with_tools(self, messages, tools, on_tool_call, max_iterations=5):
        self.messages, self.tools = messages, tools
        results = []
        for name, args in (self.script or [("get_time", {})]):
            result = on_tool_call(name, args)
            results.append({"name": name, "arguments": args, "result": result})
        last = results[-1]["result"]
        return {
            "content": "It is " + last.get("time_24h", "done"),
            "tool_results": results,
        }


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(token=None):
        settings = web.Settings(api_key="x", access_token=token)
        ai, llm = FakeAI(), FakeLLM()
        server = web.JarvisServer(settings, ai=ai, llm=llm, store=SessionStore(str(tmp_path / "s.db")))
        # Entering the client keeps every WebSocket on one event loop, like a real server.
        client = TestClient(server.build()).__enter__()
        clients.append(client)
        return client, ai, llm

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def collect(ws, until_type, channel=None):
    msgs = []
    while True:
        m = ws.receive_json()
        msgs.append(m)
        if m["type"] == until_type and (channel is None or m.get("channel") == channel):
            return msgs


def test_index_and_health(make_client):
    client, _, _ = make_client()
    assert "JARVIS" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    health = client.get("/api/health").json()
    assert health["ok"] and not health["auth_required"]


def test_auth_required_for_api_and_websocket(make_client):
    client, _, _ = make_client(token="secret")
    assert client.post("/api/auth").status_code == 401
    assert client.get("/api/tts?text=hi").status_code == 401
    # The token in a URL is no longer accepted (it would end up in access logs).
    assert client.get("/api/tts?text=hi&token=secret").status_code == 401
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=secret") as ws:
            ws.receive_json()
    # Logging in sets an HttpOnly session cookie that is not the token itself.
    r = client.post("/api/auth", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "jarvis_session=" in cookie and "HttpOnly" in cookie and "secret" not in cookie
    assert client.post("/api/auth").status_code == 200
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
    client.post("/api/logout")
    assert client.post("/api/auth").status_code == 401


def test_wrong_password_attempts_are_limited(make_client):
    client, _, _ = make_client(token="secret")
    bad = {"Authorization": "Bearer nope"}
    codes = [client.post("/api/auth", headers=bad).status_code for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429
    # Even the right password is refused while locked out.
    assert client.post("/api/auth", headers={"Authorization": "Bearer secret"}).status_code == 429


def test_chat_uses_cloud_tools_and_persists_history(make_client):
    client, _, llm = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # hello
        ws.send_json({"type": "chat", "text": "what time is it"})
        reply = collect(ws, "chat_reply")[-1]
        assert reply["text"].startswith("It is ")
        assert reply["tools"] == ["get_time"]
    assert llm.messages[0]["role"] == "system"
    # A new connection restores the conversation.
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert [m["role"] for m in hello["history"]] == ["user", "assistant"]


def test_translation_streams_with_context(make_client):
    client, ai, _ = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "translate", "id": "t1", "text": "Hello", "target": "th"})
        end = collect(ws, "stream_end", "translation")[-1]
        assert end == {"type": "stream_end", "channel": "translation", "id": "t1", "text": "[th] Hello"}
        ws.send_json({"type": "translate", "id": "t2", "text": "World", "target": "th"})
        collect(ws, "stream_end", "translation")
    assert ai.calls[1][3] == ["Hello"]


def test_lecture_notes_summary_and_questions(make_client, monkeypatch):
    monkeypatch.setattr(web, "NOTES_MIN_CHARS", 10)
    client, ai, _ = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "lecture_start", "lang": "th"})
        assert ws.receive_json()["type"] == "lecture_ready"
        ws.send_json({"type": "lecture_segment", "text": "Today we study photosynthesis."})
        notes = collect(ws, "stream_end", "notes")[-1]
        assert notes["text"] == "## Topic\n- point"
        ws.send_json({"type": "lecture_ask", "question": "what is it?"})
        assert collect(ws, "stream_end", "answer")[-1]["text"] == "answer to what is it?"
        ws.send_json({"type": "lecture_finish"})
        assert collect(ws, "stream_end", "summary")[-1]["text"] == "# Title\nsummary"
    assert ("summary", "Today we study photosynthesis.", "th") in ai.calls


def test_lecture_resume_keeps_notes_without_renoting(make_client, monkeypatch):
    monkeypatch.setattr(web, "NOTES_MIN_CHARS", 10)
    client, ai, _ = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "lecture_start", "lang": "en", "segments": ["a" * 50], "notes": "old"})
        assert ws.receive_json() == {"type": "lecture_ready", "segments": 1}
        ws.send_json({"type": "lecture_segment", "text": "new part of the lecture"})
        collect(ws, "stream_end", "notes")
    kind, notes, new_text, lang = ai.calls[0]
    assert (kind, notes, new_text, lang) == ("notes", "old", "new part of the lecture", "en")


def test_errors_are_reported(make_client):
    client, _, _ = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "lecture_finish"})
        assert ws.receive_json()["channel"] == "summary"


def test_transcribe_without_whisper_returns_501(make_client, monkeypatch):
    monkeypatch.setattr(web.WhisperTranscriber, "available", staticmethod(lambda: False))
    client, _, _ = make_client()
    assert client.post("/api/transcribe", content=b"abc").status_code == 501


def test_helpers():
    assert language_name("th") == "Thai" and language_name("en-US") == "English"
    text = "word " * 1000
    chunks = _chunks(text, 300)
    assert all(len(c) <= 300 for c in chunks)
    assert " ".join(chunks).split() == text.split()


# --------------------------------------------------------------------------- #
# PC agent
# --------------------------------------------------------------------------- #
AGENT_AUTH = {"Authorization": "Bearer secret"}


def connect_agent(client, host="my-pc"):
    ws = client.websocket_connect("/agent", headers=AGENT_AUTH)
    agent = ws.__enter__()
    agent.send_json({"type": "hello", "host": host, "os": "Windows", "screen": [200, 100]})
    return ws, agent


def test_agent_needs_the_password(make_client):
    client, _, _ = make_client(token="secret")
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        with pytest.raises(Exception):
            with client.websocket_connect("/agent", headers=headers):
                pass
    # A token in the URL does not count either.
    with pytest.raises(Exception):
        with client.websocket_connect("/agent?token=secret"):
            pass


def test_agent_refused_when_no_password_is_configured(make_client):
    client, _, _ = make_client(token=None)
    with pytest.raises(Exception):
        with client.websocket_connect("/agent", headers=AGENT_AUTH):
            pass


def test_agent_status_reaches_browsers(make_client):
    client, _, _ = make_client(token="secret")
    client.post("/api/auth", headers=AGENT_AUTH)  # log the browser in
    with client.websocket_connect("/ws") as browser:
        assert browser.receive_json()["agent"]["online"] is False
        ctx, _agent = connect_agent(client)
        try:
            status = browser.receive_json()
            assert status == {"type": "agent_status", "online": True, "host": "my-pc", "os": "Windows"}
            assert client.get("/api/health").json()["agent"]["online"] is True
        finally:
            ctx.__exit__(None, None, None)
        assert browser.receive_json()["online"] is False


def test_chat_without_agent_has_no_computer_tools(make_client):
    client, _, llm = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat", "text": "open spotify"})
        collect(ws, "chat_reply")
    assert "open_app" not in [t["function"]["name"] for t in llm.tools]
    assert "# The user's computer" not in llm.messages[0]["content"]


def test_chat_controls_the_computer_through_the_agent(make_client):
    client, _, llm = make_client(token="secret")
    llm.script = [("open_app", {"app_name": "spotify"})]
    client.post("/api/auth", headers=AGENT_AUTH)
    ctx, agent = connect_agent(client)
    try:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "open spotify"})
            call = agent.receive_json()
            assert (call["type"], call["name"], call["args"]) == ("call", "open_app", {"app_name": "spotify"})
            agent.send_json({"type": "result", "id": call["id"], "result": {"success": True}})
            reply = collect(ws, "chat_reply")[-1]
            assert reply["tools"] == ["open_app"] and not reply["error"]
        names = [t["function"]["name"] for t in llm.tools]
        assert {"open_app", "click_on", "look_at_screen", "search_web"} <= set(names)
        assert "shutdown" not in str(llm.tools) and "restart" not in str(llm.tools)
        assert "# The user's computer" in llm.messages[0]["content"]
    finally:
        ctx.__exit__(None, None, None)


def test_click_on_uses_vision_and_scales_to_the_real_screen(make_client):
    client, _, llm = make_client(token="secret")
    llm.script = [("click_on", {"description": "the play button"})]
    client.post("/api/auth", headers=AGENT_AUTH)
    ctx, agent = connect_agent(client)
    try:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "press play"})
            shot = agent.receive_json()
            assert shot["name"] == "screenshot"
            agent.send_json({"type": "result", "id": shot["id"], "result": {
                "success": True, "image_b64": "AAAA", "image_width": 100, "image_height": 50,
                "screen_width": 200, "screen_height": 100}})
            click = agent.receive_json()
            # Vision said (50, 25) in the 100x50 image -> (100, 50) on the 200x100 screen.
            assert (click["name"], click["args"]) == ("click", {"x": 100, "y": 50, "clicks": 1})
            agent.send_json({"type": "result", "id": click["id"], "result": {"success": True}})
            assert collect(ws, "chat_reply")[-1]["tools"] == ["click_on"]
    finally:
        ctx.__exit__(None, None, None)


def test_click_on_rejects_positions_outside_the_screen(make_client):
    client, _, llm = make_client(token="secret")
    llm.script = [("click_on", {"description": "x"})]
    llm.vision_reply = '```json\n{"found": true, "x": 900, "y": 25}\n```'
    client.post("/api/auth", headers=AGENT_AUTH)
    ctx, agent = connect_agent(client)
    try:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "click"})
            shot = agent.receive_json()
            agent.send_json({"type": "result", "id": shot["id"], "result": {
                "success": True, "image_b64": "AAAA", "image_width": 100, "image_height": 50,
                "screen_width": 200, "screen_height": 100}})
            reply = collect(ws, "chat_reply")[-1]
            assert reply["tools"] == ["click_on"]
    finally:
        ctx.__exit__(None, None, None)
    # No click was ever sent to the agent: nothing else is waiting for it.
