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

        # --- Wire action handlers into brain's tool dispatch ---
        # Register all ACTION_REGISTRY handlers with the brain's tool system.
        register_handlers(dict(ACTION_REGISTRY))

        # --- State ---
        self._stop = threading.Event()
        self._processing = threading.Lock()  # prevent overlapping commands

        # --- Startup greeting ---
        self._started = False

    # ------------------------------------------------------------------ #
    # Transcript handler — the brain of JARVIS
    # ------------------------------------------------------------------ #
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

            # Add user message to conversation history.
            self.history.add_user(text)

            # Get messages + tools for LLM.
            messages = self.history.get_messages()

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

    app = JarvisApp(
        config,
        lang_override=args.lang,
        model_override=args.model,
    )

    # Override LLM model if specified.
    if args.llm_model:
        app.llm.model = args.llm_model

    # Graceful Ctrl+C.
    def _sigint(*_):
        app.shutdown()
    signal.signal(signal.SIGINT, _sigint)

    if args.text_mode:
        app._text_mode_loop()
        return 0

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())