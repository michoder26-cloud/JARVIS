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


class FakeLLM:
    def __init__(self):
        self.messages = None

    def chat_with_tools(self, messages, tools, on_tool_call, max_iterations=5):
        self.messages = messages
        result = on_tool_call("get_time", {})
        return {
            "content": "It is " + result.get("time_24h", "?"),
            "tool_results": [{"name": "get_time", "arguments": {}, "result": result}],
        }


@pytest.fixture
def make_client(tmp_path):
    def _make(token=None):
        settings = web.Settings(api_key="x", access_token=token)
        ai, llm = FakeAI(), FakeLLM()
        server = web.JarvisServer(settings, ai=ai, llm=llm, store=SessionStore(str(tmp_path / "s.db")))
        return TestClient(server.build()), ai, llm

    return _make


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
    assert client.post("/api/auth", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/api/tts?text=hi&token=wrong").status_code == 401
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=wrong") as ws:
            ws.receive_json()
    with client.websocket_connect("/ws?token=secret") as ws:
        assert ws.receive_json()["type"] == "hello"


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
