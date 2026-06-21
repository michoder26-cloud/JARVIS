#!/usr/bin/env python3
"""JARVIS — voice-controlled AI assistant (CLI entry point).

Full integration: STT → LLM Brain (function calling) → Actions → TTS.

Usage
-----
    jarvis [--config PATH] [--verbose] [--lang th|en|de] [--model MODEL]

The assistant listens for the wake word "Jarvis", transcribes speech to
text, sends it to the LLM brain which decides what action to take (via
function calling), executes the action, and speaks the response.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Package bootstrap
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import yaml  # type: ignore

from jarvis.stt import STTListener
from jarvis.tts import TTSSpeaker
from jarvis.utils import list_input_devices, get_default_input_device
from jarvis.brain import LLMClient, JARVIS_PROMPT, ALL_TOOLS, ConversationHistory
from jarvis.brain.tools import register_handlers, dispatch_tool
from jarvis.actions.registry import ACTION_REGISTRY, execute_action
from jarvis.actions.memory import MEMORY_FILE
from jarvis.security import InjectionScanner
from jarvis.sessions import SessionStore
from jarvis.digest import morning_briefing

logger = logging.getLogger("jarvis")

DEFAULT_CONFIG = _HERE / "config.yaml"

BANNER = r"""
    ____  ___  ____ _ _____ ____    _    ____
   / __ \/ _ \/ ___(_) ___// __ \  | |  / ___|
  / / / / /_/ / /  / /\__ \/ / / / | |  \__ \
 / /_/ / _, _/ /__/ /___/ / /_/ /  | |___/ /
/_____/_/ |_|\____/_//____/\___\_\  |______/

   Voice-controlled AI assistant — Full System
"""


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    """Load a YAML config file, returning a plain dict."""
    cfg_path = path if path.exists() else DEFAULT_CONFIG
    if not cfg_path.exists():
        logger.warning("No config file found at %s — using built-in defaults.", cfg_path)
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class JarvisApp:
    """Top-level JARVIS application: STT → LLM → Actions → TTS."""

    def __init__(
        self,
        config: dict,
        lang_override: Optional[str] = None,
        model_override: Optional[str] = None,
        new_session: bool = False,
        session_id: str = "default",
    ) -> None:
        self.config = config
        stt_cfg = config.get("stt", {})
        tts_cfg = config.get("tts", {})
        llm_cfg = config.get("llm", {})

        language = lang_override or stt_cfg.get("language", "auto")
        stt_model = model_override or stt_cfg.get("model", "base")

        # --- TTS speaker ---
        self.speaker = TTSSpeaker(
            voice=tts_cfg.get("voice", "th-TH-PremwadeeNeural"),
            rate=int(tts_cfg.get("rate", 0)),
            volume=int(tts_cfg.get("volume", 0)),
        )

        # --- STT listener ---
        self.listener = STTListener(
            model=stt_model,
            language=language,
            wake_word=stt_cfg.get("wake_word", "jarvis"),
            silence_threshold=float(stt_cfg.get("silence_threshold", 0.5)),
            on_transcript=self._on_transcript,
        )

        # --- LLM brain ---
        llm_model = llm_cfg.get("model", "glm-5.2")
        llm_base_url = llm_cfg.get("base_url", "https://ollama.com/v1")
        self.llm = LLMClient(
            model=llm_model,
            base_url=llm_base_url,
        )

        # --- Conversation history ---
        self.history = ConversationHistory(
            system_prompt=JARVIS_PROMPT,
            max_messages=20,
        )

        # --- Session persistence (SQLite) ---
        self.session_id = session_id
        self.session_store = SessionStore()
        self._restored_count = 0
        if not new_session:
            try:
                past_msgs = self.session_store.messages_as_history(
                    session_id, limit=50
                )
                for m in past_msgs:
                    if m["role"] == "user":
                        self.history.add_user(m["content"])
                    elif m["role"] == "assistant":
                        self.history.add_assistant(m["content"])
                self._restored_count = len(past_msgs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to restore session history: %s", exc)
        else:
            # Fresh session — clear any prior messages for this id.
            try:
                self.session_store.clear_session(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to clear session %s: %s", session_id, exc)

        # --- Wire action handlers into brain's tool dispatch ---
        # Register all ACTION_REGISTRY handlers with the brain's tool system.
        register_handlers(dict(ACTION_REGISTRY))

        # --- Security: prompt-injection scanner ---
        self.scanner = InjectionScanner()

        # --- State ---
        self._stop = threading.Event()
        self._processing = threading.Lock()  # prevent overlapping commands
        self._vision_loop = None  # active VisionActionLoop, if any

        # --- Startup greeting ---
        self._started = False

    # ------------------------------------------------------------------ #
    # Transcript handler — the brain of JARVIS
    # ------------------------------------------------------------------ #
    def _load_memory(self) -> str:
        """Read persistent memory and return it as a context string.

        Returns an empty string if there is no memory file or it is empty
        so that callers can simply prepend the result to the system prompt.
        """
        try:
            if MEMORY_FILE.exists():
                text = MEMORY_FILE.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load memory file %s: %s", MEMORY_FILE, exc)
        return ""

    @staticmethod
    def _inject_memory_context(messages: list, memory_text: str) -> list:
        """Return a copy of *messages* with persistent memory injected.

        The memory is appended to the system prompt (the first message
        with ``role == "system"``) so the model sees it as additional
        context rather than a user turn.
        """
        if not memory_text:
            return messages
        memory_block = (
            "\n# Persistent Memory\n"
            "The following facts and preferences have been remembered "
            "across sessions. Use them to personalise responses but do not "
            "mention the memory file itself to the user.\n\n"
            f"{memory_text}\n"
        )
        updated = []
        for msg in messages:
            if msg.get("role") == "system" and "content" in msg:
                new_msg = dict(msg)
                new_msg["content"] = msg["content"] + memory_block
                updated.append(new_msg)
            else:
                updated.append(msg)
        return updated

    def _on_transcript(self, text: str) -> None:
        """Handle a recognised command: send to LLM, execute tools, speak."""
        if not text.strip():
            return

        # Prevent overlapping command processing.
        if not self._processing.acquire(blocking=False):
            logger.warning("Ignoring command — already processing one.")
            return

        try:
            print(f"\n[YOU] {text}")

            # --- Security: scan user input for prompt-injection attempts ---
            scan_result = self.scanner.scan(text)
            if scan_result["has_threat"]:
                level = scan_result["level"]
                logger.warning(
                    "Prompt-injection scan: level=%s threats=%s",
                    level,
                    [t["pattern_name"] for t in scan_result["threats"]],
                )
                if level == "high":
                    # Block the input — do not send it to the LLM.
                    reject = "คำสั่งนี้ดูไม่ปลอดภัย ขอข้ามไปครับ"
                    print(f"[JARVIS] {reject}")
                    self.speaker.speak(reject)
                    return
                # medium / low: log a warning but continue processing.

            # Add user message to conversation history.
            self.history.add_user(text)
            # Persist user message to SQLite session store.
            try:
                self.session_store.save_message(
                    self.session_id, "user", text
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist user message: %s", exc)

            # Get messages + tools for LLM, injecting persistent memory as
            # additional context for the model.
            messages = self.history.get_messages()
            memory_text = self._load_memory()
            if memory_text:
                messages = self._inject_memory_context(messages, memory_text)

            # Call LLM with function calling.
            response = self.llm.chat_with_tools(
                messages=messages,
                tools=ALL_TOOLS,
                on_tool_call=self._dispatch_action,
                max_iterations=5,
            )

            # Speak the response.
            reply = response.get("content", "").strip()
            if reply:
                print(f"[JARVIS] {reply}")
                self.speaker.speak(reply)
                self.history.add_assistant(reply)
                # Persist assistant reply to SQLite session store.
                try:
                    self.session_store.save_message(
                        self.session_id, "assistant", reply
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to persist assistant message: %s", exc)
            else:
                # No text response — check if tools were called.
                tool_results = response.get("tool_results", [])
                if tool_results:
                    # Tools were executed but LLM didn't give a text reply.
                    # Generate a simple confirmation.
                    names = [tr["name"] for tr in tool_results]
                    logger.info("Tools executed: %s", names)
                else:
                    logger.warning("LLM returned no content and no tool calls.")

        except Exception as exc:
            logger.exception("Error processing command")
            error_msg = "ขออภัย เกิดข้อผิดพลาด ครับ"  # Thai: "Sorry, an error occurred"
            print(f"[ERROR] {exc}")
            self.speaker.speak(error_msg)
        finally:
            self._processing.release()

    # ------------------------------------------------------------------ #
    # Vision-action loop — autonomous multi-step tasks
    # ------------------------------------------------------------------ #
    def _run_visual_task(self, command: str) -> dict:
        """Create a VisionActionLoop and run a complex multi-step task.

        Called from :meth:`_dispatch_action` when the LLM invokes the
        ``execute_visual_task`` tool.  Uses the live LLM, speaker, and a
        lazily-created ScreenVision instance.
        """
        if not command:
            return {"success": False, "error": "No command provided."}
        try:
            from jarvis.vision.loop import VisionActionLoop
            from jarvis.vision.eyes import ScreenVision
        except Exception as exc:  # noqa: BLE001
            logger.error("Vision modules unavailable: %s", exc)
            return {
                "success": False,
                "error": f"Vision unavailable: {exc}",
            }

        try:
            eyes = ScreenVision()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ScreenVision init failed: %s", exc)
            eyes = None

        loop = VisionActionLoop(
            llm_client=self.llm,
            speaker=self.speaker,
            screen_vision=eyes,
            max_steps=10,
        )
        self._vision_loop = loop  # expose so a "stop" command can halt it
        try:
            result = loop.execute_task(command)
        finally:
            self._vision_loop = None
        logger.info("VisionActionLoop result: %s", result.get("success"))
        return result

    # ------------------------------------------------------------------ #
    # Action dispatch — called by LLM function calling
    # ------------------------------------------------------------------ #
    def _dispatch_action(self, name: str, args: dict) -> dict:
        """Dispatch a tool call to the appropriate action handler.

        Special handling for 'ask_user' which needs to speak + listen.
        """
        # --- Special case: ask_user ---
        if name == "ask_user":
            question = args.get("question", "")
            if question:
                print(f"[JARVIS] ❓ {question}")
                self.speaker.speak(question)
            # The next utterance from the user (via wake word) will be the answer.
            # For now, return a placeholder — the LLM will get the user's next
            # message as a new turn.
            return {
                "success": True,
                "result": "Question asked. Waiting for user response.",
                "error": None,
            }

        # --- Special case: screenshot ---
        # The brain/tools.py has 'screenshot' but actions has both 'screenshot'
        # (desktop) and 'take_browser_screenshot' (browser). Use desktop one.
        if name == "screenshot":
            handler = ACTION_REGISTRY.get("screenshot")
            if handler:
                return handler()

        # --- Special case: execute_visual_task (vision-action loop) ---
        # Needs live access to the LLM, speaker, and ScreenVision, so it
        # is wired here rather than via the static ACTION_REGISTRY.
        if name == "execute_visual_task":
            return self._run_visual_task(args.get("command", ""))

        # --- Default: dispatch via execute_action ---
        result = execute_action(name, args)
        logger.info("Action %s result: %s", name, result.get("success"))
        return result

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        """Start the listener and block until interrupted."""
        print(BANNER)
        if self._restored_count > 0:
            print(f"Restored {self._restored_count} messages from last session.")
        print("JARVIS initialised. Say 'Jarvis' to give a command.\n")

        # Quick audio device sanity check.
        default_in = get_default_input_device()
        if default_in is None:
            print("⚠ No microphone detected. STT will not work in this environment.")
            logger.warning("No default input device found.")
        else:
            logger.info("Using input device: %s", default_in.get("name"))

        # Startup greeting (only once).
        if not self._started:
            self._started = True
            greeting = "JARVIS online. พร้อมรับคำสั่งครับ"
            print(f"[JARVIS] {greeting}")
            # Speak greeting in a background thread (non-blocking).
            threading.Thread(
                target=self.speaker.speak,
                args=(greeting,),
                daemon=True,
            ).start()

        # Start listening.
        try:
            self.listener.start()
        except RuntimeError as exc:
            print(f"⚠ STT unavailable: {exc}")
            print("  JARVIS will run in text-only mode.")
            self._text_mode_loop()
            return

        # Block until Ctrl+C.
        try:
            while not self._stop.is_set():
                self._stop.wait(timeout=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.listener.stop()
            print("\nJARVIS shutting down. Goodbye!")

    def _text_mode_loop(self) -> None:
        """Fallback: text-only mode when STT is unavailable."""
        print("\n--- Text Mode --- (type commands, Ctrl+C to exit)\n")
        while not self._stop.is_set():
            try:
                text = input("[YOU] ").strip()
                if not text:
                    continue
                self._on_transcript(text)
            except (EOFError, KeyboardInterrupt):
                break

    def shutdown(self) -> None:
        """Signal the main loop to exit."""
        self._stop.set()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI."""
    p = argparse.ArgumentParser(
        prog="jarvis",
        description="JARVIS — voice-controlled AI assistant.",
    )
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                   help="Path to config.yaml")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Enable DEBUG logging")
    p.add_argument("--lang", choices=["th", "en", "de", "auto"], default=None,
                   help="Override STT language")
    p.add_argument("--model", default=None,
                   help="Override Whisper model (tiny/base/small/medium/large-v3)")
    p.add_argument("--llm-model", default=None,
                   help="Override LLM model (e.g. glm-5.2, minimax-m3)")
    p.add_argument("--list-audio", action="store_true",
                   help="List audio devices and exit")
    p.add_argument("--text-mode", action="store_true",
                   help="Start in text-only mode (no microphone needed)")
    p.add_argument("--briefing", action="store_true",
                   help="Generate and speak a morning briefing, then exit")
    p.add_argument("--location", default="Bangkok",
                   help="Location for briefing weather")
    p.add_argument("--new-session", action="store_true",
                   help="Start a fresh session (don't load old messages)")
    p.add_argument("--sessions", action="store_true",
                   help="List all stored sessions and exit")
    return p


def setup_logging(verbose: bool, level_name: str = "INFO") -> None:
    """Configure root logging."""
    level = logging.DEBUG if verbose else getattr(
        logging, level_name.upper(), logging.INFO
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns an exit code."""
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    setup_logging(args.verbose, config.get("logging", {}).get("level", "INFO"))

    if args.list_audio:
        from jarvis.utils.audio import print_audio_info
        print_audio_info()
        return 0

    # --sessions: list sessions and exit.
    if args.sessions:
        store = SessionStore()
        sessions = store.list_sessions()
        if not sessions:
            print("No sessions found.")
        else:
            print(f"Sessions ({len(sessions)}):")
            for s in sessions:
                print(
                    f"  {s['id']:<20} created={s['created_at']} "
                    f"updated={s['updated_at']}"
                )
        return 0

    app = JarvisApp(
        config,
        lang_override=args.lang,
        model_override=args.model,
        new_session=args.new_session,
    )

    # Override LLM model if specified.
    if args.llm_model:
        app.llm.model = args.llm_model

    # Graceful Ctrl+C.
    def _sigint(*_):
        app.shutdown()
    signal.signal(signal.SIGINT, _sigint)

    # --briefing: generate and speak a morning briefing, then exit.
    if args.briefing:
        print("Generating morning briefing...")
        text = morning_briefing(app.llm, app.speaker, location=args.location)
        print(f"[BRIEFING] {text}")
        return 0

    if args.text_mode:
        app._text_mode_loop()
        return 0

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())