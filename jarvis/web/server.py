"""JARVIS cloud web server.

Runs JARVIS on a server so it can be used from any phone or laptop browser:

* **Assistant** — chat by voice or text with web search, news, weather,
  time and long-term memory.
* **Live Translate** — speech is transcribed in the browser and each phrase
  is translated and streamed back as it is spoken.
* **Lecture** — a live transcript of a class, notes that update while the
  teacher speaks, a final summary, and questions about the lecture.

Speech recognition runs in the browser (Web Speech API) by default; the
``/api/transcribe`` endpoint offers server-side Whisper for browsers that
lack it. Replies are spoken with ``/api/tts`` (edge-tts neural voices).

Run with ``jarvis --web`` (or ``python -m jarvis.web``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from jarvis.actions.memory import inject_memory_context, load_memory_text, memory_manage, think
from jarvis.actions.news import get_news
from jarvis.actions.search import search_web_api
from jarvis.actions.system import get_time, get_weather
from jarvis.brain import ConversationHistory, JARVIS_WEB_PROMPT, LLMClient
from jarvis.brain.providers import check_models, resolve_llm
from jarvis.brain.tools import GET_NEWS, GET_TIME, GET_WEATHER, MEMORY_MANAGE, SEARCH_WEB, THINK
from jarvis.sessions import SessionStore
from jarvis.tts.speaker import TTSSpeaker
from jarvis.web.ai import AIService
from jarvis.web.computer import (
    COMPUTER_PROMPT, COMPUTER_TOOLS, AgentHub, ComputerTools, resolve_vision_model,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Tools that make sense on a server without a screen.
CLOUD_TOOLS = [SEARCH_WEB, GET_NEWS, GET_WEATHER, GET_TIME, MEMORY_MANAGE, THINK]
CLOUD_HANDLERS = {
    "search_web": search_web_api,
    "get_news": get_news,
    "get_weather": get_weather,
    "get_time": get_time,
    "memory_manage": memory_manage,
    "think": think,
}

# Live notes refresh once this many new transcript characters arrive.
NOTES_MIN_CHARS = 600
# Number of previous phrases sent as context with each translation.
TRANSLATE_CONTEXT = 3
# Limits that keep a single request from running away with the API budget.
MAX_TEXT_CHARS = 4_000
MAX_TTS_CHARS = 1_500
MAX_AUDIO_BYTES = 25 * 1024 * 1024
WEB_SESSION_ID = "web"
SESSION_COOKIE = "jarvis_session"
SESSION_MAX_AGE = 180 * 24 * 3600
# Wrong-password attempts allowed per IP within FAILURE_WINDOW seconds.
MAX_FAILURES = 8
FAILURE_WINDOW = 15 * 60


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    """Server settings, read from config.yaml and environment variables."""

    llm_model: str = "gemini-3.8-flash"
    fast_model: Optional[str] = None
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    api_key: Optional[str] = None
    provider: str = "gemini"
    key_env: str = "GEMINI_API_KEY"
    vision_model: Optional[str] = None
    access_token: Optional[str] = None
    tts_voice: str = "th-TH-PremwadeeNeural"
    whisper_model: str = "base"

    @classmethod
    def from_config(cls, config: Optional[dict] = None) -> "Settings":
        llm = check_models(resolve_llm(config))
        tts = (config or {}).get("tts", {})
        stt = (config or {}).get("stt", {})
        env = os.environ.get
        return cls(
            llm_model=llm.model,
            fast_model=llm.fast_model,
            base_url=llm.base_url,
            api_key=llm.api_key,
            provider=llm.provider,
            key_env=llm.key_env,
            vision_model=resolve_vision_model(llm.provider, llm.model, os.environ.get("JARVIS_VISION_MODEL")),
            access_token=env("JARVIS_ACCESS_TOKEN") or None,
            tts_voice=env("JARVIS_TTS_VOICE") or tts.get("voice", "th-TH-PremwadeeNeural"),
            whisper_model=env("JARVIS_WHISPER_MODEL") or stt.get("model", "base"),
        )


# --------------------------------------------------------------------------- #
# Server-side speech recognition (optional faster-whisper)
# --------------------------------------------------------------------------- #
class WhisperTranscriber:
    """Lazily loaded faster-whisper model shared by all requests."""

    def __init__(self, model_size: str) -> None:
        self.model_size = model_size
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def transcribe(self, audio: bytes, language: Optional[str]) -> str:
        from faster_whisper import WhisperModel

        with self._lock:
            if self._model is None:
                logger.info("Loading Whisper model %s ...", self.model_size)
                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            with tempfile.NamedTemporaryFile(suffix=".audio") as tmp:
                tmp.write(audio)
                tmp.flush()
                segments, _info = self._model.transcribe(
                    tmp.name,
                    language=language or None,
                    vad_filter=True,
                    beam_size=1,
                )
                return " ".join(s.text.strip() for s in segments).strip()


# --------------------------------------------------------------------------- #
# Per-connection state
# --------------------------------------------------------------------------- #
@dataclass
class LectureState:
    lang: str = "th"
    segments: List[str] = field(default_factory=list)
    notes: str = ""
    noted_upto: int = 0  # segments[:noted_upto] are reflected in notes
    notes_task: Optional[asyncio.Task] = None

    def pending_text(self) -> str:
        return "\n".join(self.segments[self.noted_upto:])

    def transcript(self) -> str:
        return "\n".join(self.segments)


class Connection:
    """One browser tab connected over WebSocket."""

    def __init__(self, ws: WebSocket, server: "JarvisServer") -> None:
        self.ws = ws
        self.server = server
        self._send_lock = asyncio.Lock()
        self._chat_lock = asyncio.Lock()
        self.tasks: Set[asyncio.Task] = set()
        self.recent_phrases: List[str] = []
        self.lecture: Optional[LectureState] = None
        self.history = ConversationHistory(system_prompt=JARVIS_WEB_PROMPT, max_messages=30)
        self._restore_history()

    def _restore_history(self) -> None:
        try:
            for m in self.server.store.messages_as_history(WEB_SESSION_ID, limit=30):
                if m["role"] == "user":
                    self.history.add_user(m["content"])
                elif m["role"] == "assistant":
                    self.history.add_assistant(m["content"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not restore web chat history: %s", exc)

    async def send(self, payload: Dict[str, Any]) -> None:
        async with self._send_lock:
            try:
                await self.ws.send_json(payload)
            except Exception:  # noqa: BLE001 — socket closed mid-stream
                pass

    async def error(self, message: str, channel: Optional[str] = None, id: Any = None) -> None:
        await self.send({"type": "error", "message": message, "channel": channel, "id": id})

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()

    async def stream_to_client(self, channel: str, gen, id: Any = None) -> str:
        """Forward an async text generator to the client; return the full text."""
        await self.send({"type": "stream_start", "channel": channel, "id": id})
        parts: List[str] = []
        try:
            async for delta in gen:
                parts.append(delta)
                await self.send({"type": "stream", "channel": channel, "id": id, "delta": delta})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s stream failed: %s", channel, exc)
            await self.error(_friendly_error(exc), channel=channel, id=id)
        text = "".join(parts).strip()
        await self.send({"type": "stream_end", "channel": channel, "id": id, "text": text})
        return text

    # ------------------------------------------------------------------ #
    # Message handlers
    # ------------------------------------------------------------------ #
    async def handle(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "ping":
            await self.send({"type": "pong"})
        elif kind == "chat":
            self.spawn(self.on_chat(_clip(msg.get("text"))))
        elif kind == "chat_reset":
            self.history.clear()
            try:
                self.server.store.clear_session(WEB_SESSION_ID)
            except Exception:  # noqa: BLE001
                pass
            await self.send({"type": "chat_reset"})
        elif kind == "translate":
            self.spawn(self.on_translate(msg))
        elif kind == "lecture_start":
            await self.on_lecture_start(msg)
        elif kind == "lecture_segment":
            await self.on_lecture_segment(_clip(msg.get("text")))
        elif kind == "lecture_finish":
            self.spawn(self.on_lecture_finish())
        elif kind == "lecture_ask":
            self.spawn(self.on_lecture_ask(_clip(msg.get("question"))))
        else:
            await self.error(f"Unknown message type: {kind!r}")

    async def on_chat(self, text: str) -> None:
        if not text:
            return
        async with self._chat_lock:  # one turn at a time keeps history ordered
            await self.send({"type": "chat_status", "status": "thinking"})
            self.history.add_user(text)
            self.server.save_message("user", text)
            messages = inject_memory_context(self.history.get_messages(), load_memory_text())
            tools, dispatch, iterations = CLOUD_TOOLS, _dispatch_cloud_tool, 6
            if self.server.hub.online:
                messages = _add_to_system_prompt(messages, COMPUTER_PROMPT)
                tools = CLOUD_TOOLS + COMPUTER_TOOLS
                dispatch = self.server.computer_dispatcher(asyncio.get_running_loop())
                iterations = 12
            response = await asyncio.to_thread(
                self.server.llm.chat_with_tools,
                messages=messages,
                tools=tools,
                on_tool_call=dispatch,
                max_iterations=iterations,
            )
            reply = (response.get("content") or "").strip()
            tools = [t["name"] for t in response.get("tool_results", [])]
            if response.get("error"):
                logger.warning("Chat failed: %s", response["error"])
            elif reply:
                self.history.add_assistant(reply)
                self.server.save_message("assistant", reply)
            await self.send({
                "type": "chat_reply",
                "text": reply,
                "tools": tools,
                "error": bool(response.get("error")),
            })

    async def on_translate(self, msg: Dict[str, Any]) -> None:
        text = _clip(msg.get("text"))
        target = str(msg.get("target") or "th")
        if not text:
            return
        context = self.recent_phrases[-TRANSLATE_CONTEXT:]
        self.recent_phrases = (self.recent_phrases + [text])[-TRANSLATE_CONTEXT:]
        await self.stream_to_client(
            "translation",
            self.server.ai.translate(text, target, context),
            id=msg.get("id"),
        )

    async def on_lecture_start(self, msg: Dict[str, Any]) -> None:
        """Start a lecture, or resume one after a reconnect."""
        if self.lecture and self.lecture.notes_task:
            self.lecture.notes_task.cancel()
        segments = [_clip(s) for s in (msg.get("segments") or []) if isinstance(s, str)]
        notes = str(msg.get("notes") or "")
        self.lecture = LectureState(
            lang=str(msg.get("lang") or "th"),
            segments=[s for s in segments if s],
            notes=notes,
        )
        # Previously noted segments don't need re-noting after a resume.
        if notes:
            self.lecture.noted_upto = len(self.lecture.segments)
        await self.send({"type": "lecture_ready", "segments": len(self.lecture.segments)})

    async def on_lecture_segment(self, text: str) -> None:
        if not text:
            return
        if self.lecture is None:
            self.lecture = LectureState()
        self.lecture.segments.append(text)
        self._maybe_update_notes()

    def _maybe_update_notes(self, force: bool = False) -> None:
        lec = self.lecture
        if lec is None or (lec.notes_task and not lec.notes_task.done()):
            return
        pending = lec.pending_text()
        if not pending or (len(pending) < NOTES_MIN_CHARS and not force):
            return
        lec.notes_task = self.spawn(self._update_notes(lec))

    async def _update_notes(self, lec: LectureState) -> None:
        upto = len(lec.segments)
        new_text = "\n".join(lec.segments[lec.noted_upto:upto])
        notes = await self.stream_to_client(
            "notes", self.server.ai.update_notes(lec.notes, new_text, lec.lang)
        )
        if notes and self.lecture is lec:
            lec.notes = notes
            lec.noted_upto = upto
            lec.notes_task = None
            self._maybe_update_notes()  # more text may have arrived meanwhile

    async def on_lecture_finish(self) -> None:
        lec = self.lecture
        if lec is None or not lec.segments:
            await self.error("No lecture transcript yet.", channel="summary")
            return
        if lec.notes_task and not lec.notes_task.done():
            lec.notes_task.cancel()
        await self.stream_to_client("summary", self.server.ai.summarize(lec.transcript(), lec.lang))

    async def on_lecture_ask(self, question: str) -> None:
        lec = self.lecture
        if not question:
            return
        if lec is None or not lec.segments:
            await self.error("No lecture transcript yet.", channel="answer")
            return
        await self.stream_to_client(
            "answer", self.server.ai.answer(question, lec.transcript(), lec.notes, lec.lang)
        )


def _dispatch_cloud_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = CLOUD_HANDLERS.get(name)
    if handler is None:
        return {"success": False, "error": f"Tool '{name}' is not available in the cloud."}
    try:
        return handler(**(args if isinstance(args, dict) else {}))
    except TypeError as exc:
        return {"success": False, "error": f"Invalid arguments for '{name}': {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def _add_to_system_prompt(messages: List[Dict[str, Any]], extra: str) -> List[Dict[str, Any]]:
    """Return *messages* with *extra* appended to the system message."""
    out = []
    for msg in messages:
        if msg.get("role") == "system" and isinstance(msg.get("content"), str):
            msg = {**msg, "content": msg["content"] + extra}
        out.append(msg)
    return out


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _clip(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return str(value or "").strip()[:limit]


def _friendly_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(x in msg for x in ("401", "403", "unauthorized", "api key", "permission")):
        return "AI API key is missing or invalid — check the key set on the server."
    if "429" in msg or "quota" in msg or "rate limit" in msg:
        return "AI rate limit reached (free tier). Wait a minute and try again."
    if "404" in msg or "not found" in msg:
        return "AI model not found — set JARVIS_LLM_MODEL to a model your provider offers."
    if "timeout" in msg or "timed out" in msg:
        return "The AI took too long to answer. Please try again."
    if "connect" in msg:
        return "Cannot reach the AI service."
    return "AI request failed."


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
class JarvisServer:
    """Holds shared services; :meth:`build` returns the FastAPI app."""

    def __init__(
        self,
        settings: Settings,
        ai: Optional[AIService] = None,
        llm: Optional[LLMClient] = None,
        store: Optional[SessionStore] = None,
    ) -> None:
        self.settings = settings
        self.ai = ai or AIService(
            model=settings.llm_model,
            fast_model=settings.fast_model,
            base_url=settings.base_url,
            api_key=settings.api_key,
        )
        self.llm = llm or LLMClient(
            model=settings.llm_model,
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=90,
        )
        self.store = store or SessionStore()
        self.whisper = WhisperTranscriber(settings.whisper_model)
        self._failures: Dict[str, List[float]] = {}
        self.hub = AgentHub()
        self.connections: Set["Connection"] = set()

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        for conn in list(self.connections):
            await conn.send(payload)

    def computer_dispatcher(self, loop: asyncio.AbstractEventLoop) -> ComputerTools:
        return ComputerTools(
            self.hub, loop, self.llm._ensure_client(), self.settings.vision_model or self.settings.llm_model
        )

    def save_message(self, role: str, content: str) -> None:
        try:
            self.store.save_message(WEB_SESSION_ID, role, content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist %s message: %s", role, exc)

    def authorized(self, token: Optional[str]) -> bool:
        expected = self.settings.access_token
        if not expected:
            return True
        return bool(token) and secrets.compare_digest(token, expected)

    def session_value(self) -> str:
        """Cookie value proving a successful login.

        Derived from the access token, so the token itself is never stored
        in the browser, and changing the token logs every device out.
        """
        key = (self.settings.access_token or "").encode()
        return hmac.new(key, b"jarvis-session-v1", hashlib.sha256).hexdigest()

    def session_ok(self, cookies: Dict[str, str]) -> bool:
        if not self.settings.access_token:
            return True
        value = cookies.get(SESSION_COOKIE, "")
        return bool(value) and secrets.compare_digest(value, self.session_value())

    def _bearer(self, request: Request) -> Optional[str]:
        auth = request.headers.get("authorization", "")
        return auth[7:] if auth.lower().startswith("bearer ") else None

    def _check(self, request: Request) -> None:
        if self.session_ok(request.cookies):
            return
        token = self._bearer(request)
        if token is None or not self.authorized(token):
            raise HTTPException(status_code=401, detail="Invalid access token")

    def _too_many_failures(self, ip: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._failures.get(ip, []) if now - t < FAILURE_WINDOW]
        self._failures[ip] = recent
        return len(recent) >= MAX_FAILURES

    def build(self) -> FastAPI:
        app = FastAPI(title="JARVIS", docs_url=None, redoc_url=None)

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

        @app.get("/manifest.webmanifest")
        async def manifest() -> FileResponse:
            return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

        @app.get("/api/health")
        async def health() -> Dict[str, Any]:
            return {
                "ok": True,
                "llm_configured": self.ai.configured,
                "model": self.settings.llm_model,
                "provider": self.settings.provider,
                "key_env": self.settings.key_env,
                "auth_required": bool(self.settings.access_token),
                "agent": self.hub.status(),
                "server_stt": WhisperTranscriber.available(),
            }

        @app.post("/api/auth")
        async def auth(request: Request) -> Response:
            """Log in with ``Authorization: Bearer <token>``, or check the cookie."""
            if self.session_ok(request.cookies) and self._bearer(request) is None:
                return JSONResponse({"ok": True})
            ip = _client_ip(request)
            if self._too_many_failures(ip):
                raise HTTPException(status_code=429, detail="Too many attempts — try again later")
            if not self.authorized(self._bearer(request)):
                self._failures.setdefault(ip, []).append(time.monotonic())
                raise HTTPException(status_code=401, detail="Invalid access token")
            response = JSONResponse({"ok": True})
            if self.settings.access_token:
                secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
                response.set_cookie(
                    SESSION_COOKIE, self.session_value(), max_age=SESSION_MAX_AGE,
                    httponly=True, secure=secure, samesite="strict",
                )
            return response

        @app.post("/api/logout")
        async def logout() -> Response:
            response = JSONResponse({"ok": True})
            response.delete_cookie(SESSION_COOKIE)
            return response

        @app.get("/api/tts")
        async def tts(request: Request, text: str, voice: Optional[str] = None) -> Response:
            self._check(request)
            text = text.strip()[:MAX_TTS_CHARS]
            if not text:
                raise HTTPException(status_code=400, detail="Empty text")
            speaker = TTSSpeaker(voice=self.settings.tts_voice)
            try:
                audio = await speaker._speak_async(text, voice)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TTS failed: %s", exc)
                raise HTTPException(status_code=502, detail="TTS failed") from exc
            if not audio:
                raise HTTPException(status_code=502, detail="TTS produced no audio")
            return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

        @app.post("/api/transcribe")
        async def transcribe(request: Request, lang: Optional[str] = None) -> JSONResponse:
            self._check(request)
            if not WhisperTranscriber.available():
                raise HTTPException(
                    status_code=501,
                    detail="Server speech recognition is not installed (pip install faster-whisper).",
                )
            audio = await request.body()
            if not audio:
                raise HTTPException(status_code=400, detail="Empty audio")
            if len(audio) > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="Audio too large")
            language = (lang or "").split("-")[0] or None
            try:
                text = await asyncio.to_thread(self.whisper.transcribe, audio, language)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Transcription failed: %s", exc)
                raise HTTPException(status_code=500, detail="Transcription failed") from exc
            return JSONResponse({"text": text})

        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket) -> None:
            if not self.session_ok(ws.cookies):
                await ws.close(code=4401)
                return
            await ws.accept()
            conn = Connection(ws, self)
            self.connections.add(conn)
            await conn.send({
                "type": "hello",
                "llm_configured": self.ai.configured,
                "key_env": self.settings.key_env,
                "agent": self.hub.status(),
                "history": conn.history.messages[-20:],
            })
            try:
                while True:
                    raw = await ws.receive_text()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        await conn.error("Invalid JSON")
                        continue
                    if isinstance(msg, dict):
                        await conn.handle(msg)
            except WebSocketDisconnect:
                pass
            finally:
                self.connections.discard(conn)
                await conn.close()

        @app.websocket("/agent")
        async def agent_endpoint(ws: WebSocket) -> None:
            """The PC agent connects here (password in a header, never in the URL)."""
            ip = ws.headers.get("x-forwarded-for", "").split(",")[0].strip() or (ws.client.host if ws.client else "?")
            auth = ws.headers.get("authorization", "")
            token = auth[7:] if auth.lower().startswith("bearer ") else None
            # Without a configured password anyone could take over the agent slot.
            if not self.settings.access_token or self._too_many_failures(ip) or not self.authorized(token):
                self._failures.setdefault(ip, []).append(time.monotonic())
                await ws.close(code=4401)
                return
            await ws.accept()
            info: Dict[str, Any] = {}
            try:
                hello = json.loads(await asyncio.wait_for(ws.receive_text(), 15))
                if isinstance(hello, dict) and hello.get("type") == "hello":
                    info = {k: hello.get(k) for k in ("host", "os", "screen", "actions")}
                await self.hub.attach(ws, info)
                await self.broadcast({"type": "agent_status", **self.hub.status()})
                while True:
                    msg = json.loads(await ws.receive_text())
                    if isinstance(msg, dict) and msg.get("type") == "result":
                        self.hub.handle_result(msg)
            except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
                pass
            finally:
                if self.hub.detach(ws):
                    await self.broadcast({"type": "agent_status", **self.hub.status()})

        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        return app


def create_app(config: Optional[dict] = None, **kwargs: Any) -> FastAPI:
    """Build the FastAPI app from a config dict (see ``config.yaml``)."""
    settings = Settings.from_config(config)
    if not settings.access_token:
        logger.warning(
            "JARVIS_ACCESS_TOKEN is not set — anyone who finds this URL can use "
            "your AI quota. Set it before deploying publicly."
        )
    if not settings.api_key:
        logger.warning("%s is not set — AI features will fail.", settings.key_env)
    logger.info("AI provider: %s, model: %s", settings.provider, settings.llm_model)
    return JarvisServer(settings, **kwargs).build()


def run(config: Optional[dict] = None, host: str = "0.0.0.0", port: Optional[int] = None) -> None:
    """Start the web server with uvicorn."""
    import uvicorn

    port = port or int(os.environ.get("PORT", "8000"))
    logger.info("JARVIS web on http://%s:%d", host, port)
    uvicorn.run(create_app(config), host=host, port=port, log_level="info", ws_ping_interval=20)
