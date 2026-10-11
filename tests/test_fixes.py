"""Regression tests for bugs fixed in the core assistant."""

import os
import sys
import types
from types import SimpleNamespace

from jarvis.actions.registry import execute_action
from jarvis.actions.system import get_time
from jarvis.brain.llm import LLMClient
from jarvis.sessions.store import SessionStore
from jarvis.stt.listener import STTListener
from jarvis.tts.speaker import detect_language, pick_voice


def _tool_call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=args))


def _response(content="", tool_calls=None):
    msg = SimpleNamespace(content=content, role="assistant", tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _client_with(responses):
    client = LLMClient(api_key="test")
    completions = FakeCompletions(responses)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_tool_call_without_id_is_paired_with_its_result():
    client, completions = _client_with([
        _response(tool_calls=[_tool_call(None, "get_time", "{}")]),
        _response(content="It is noon."),
    ])
    out = client.chat_with_tools(
        [{"role": "user", "content": "time?"}], tools=[], on_tool_call=lambda n, a: {"success": True}
    )
    assert out["content"] == "It is noon."
    second = completions.calls[1]["messages"]
    assistant, tool = second[-2], second[-1]
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"]


def test_tool_arguments_that_are_not_an_object_become_empty_dict():
    seen = {}
    client, _ = _client_with([
        _response(tool_calls=[_tool_call("c1", "get_time", "null")]),
        _response(content="ok"),
    ])
    client.chat_with_tools([], tools=[], on_tool_call=lambda n, a: seen.setdefault("args", a) or {})
    assert seen["args"] == {}


def test_execute_action_tolerates_non_dict_args():
    assert execute_action("get_time", None)["success"]
    assert execute_action("get_time", ["bad"])["success"]
    assert not execute_action("does_not_exist", {})["success"]


def test_listener_passes_valid_realtimestt_arguments(monkeypatch):
    captured = {}

    class FakeRecorder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "RealtimeSTT", types.SimpleNamespace(AudioToTextRecorder=FakeRecorder))
    STTListener(language="auto", silence_threshold=0.7)._ensure_recorder()
    assert captured["post_speech_silence_duration"] == 0.7
    assert captured["language"] == ""
    assert captured["device"] in ("cpu", "cuda")
    assert "post_speech_silence" not in captured
    assert "print_transcript_time" not in captured


def test_voice_follows_reply_language():
    assert detect_language("เปิด YouTube แล้วครับ") == "th"
    assert pick_voice("Opening YouTube, sir.", "th-TH-NiwatNeural") == "en-US-AriaNeural"
    assert pick_voice("เปิดแล้วครับ", "th-TH-NiwatNeural") == "th-TH-NiwatNeural"
    assert pick_voice("Wie ist das Wetter?", "th-TH-PremwadeeNeural") == "de-DE-KatjaNeural"


def test_get_time_uses_configured_timezone(monkeypatch):
    monkeypatch.setenv("JARVIS_TIMEZONE", "Asia/Bangkok")
    out = get_time()
    assert out["success"] and out["iso"].endswith("+07:00")


def test_session_store_round_trip(tmp_path):
    store = SessionStore(os.fspath(tmp_path / "s.db"))
    store.save_message("x", "user", "hi")
    store.save_message("x", "assistant", "hello")
    assert [m["role"] for m in store.messages_as_history("x")] == ["user", "assistant"]
    store.clear_session("x")
    assert store.messages_as_history("x") == []
