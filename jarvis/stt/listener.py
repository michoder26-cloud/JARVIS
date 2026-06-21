"""Speech-to-Text listener for JARVIS.

Wraps RealtimeSTT's ``AudioToTextRecorder`` (which itself uses
faster-whisper under the hood) to provide continuous speech recognition
with optional wake-word gating and push-to-talk fallback.

The module is designed so that importing it never crashes when the
audio backend is missing — the :class:`STTListener` only raises at
``start()`` time, allowing the rest of JARVIS to load in headless
environments.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Callback type: invoked with recognised text.
TranscriptCallback = Callable[[str], None]


class STTListener:
    """Continuous speech-to-text listener with wake-word support.

    Parameters
    ----------
    model : str
        Whisper model size: ``tiny``, ``base``, ``small``, ``medium``,
        ``large-v3`` (default from config: ``base``).
    language : str
        ``"auto"`` for automatic detection, or a language code like
        ``"th"``, ``"en"``, ``"de"``.
    wake_word : str
        Word the user says to activate JARVIS (default ``"jarvis"``).
        When set, transcripts that do not *start with* the wake word
        (case-insensitive) are ignored, and the wake word itself is
        stripped before being passed to ``on_transcript``.
    silence_threshold : float
        Seconds of silence after which a utterance is considered
        complete (passed to RealtimeSTT).
    on_transcript : callable, optional
        Callback ``f(text: str)`` invoked with each recognised command.
    push_to_talk : bool
        If True, skip wake-word detection and treat every utterance as
        a command (useful when triggered by a hotkey).
    """

    def __init__(
        self,
        model: str = "base",
        language: str = "auto",
        wake_word: str = "jarvis",
        silence_threshold: float = 0.5,
        on_transcript: Optional[TranscriptCallback] = None,
        push_to_talk: bool = False,
    ) -> None:
        self.model = model
        # RealtimeSTT / faster-whisper use ``None`` for auto-detect.
        self.language: Optional[str] = None if language == "auto" else language
        self.wake_word = wake_word.lower().strip()
        self.silence_threshold = silence_threshold
        self.on_transcript = on_transcript
        self.push_to_talk = push_to_talk
        self._recorder = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Last full transcript (for debugging / external polling).
        self.last_transcript: str = ""

    # ------------------------------------------------------------------ #
    # Wake-word logic
    # ------------------------------------------------------------------ #
    def _extract_command(self, text: str) -> Optional[str]:
        """Check *text* for the wake word and return the command portion.

        Returns ``None`` if the wake word is not present (and push-to-talk
        is disabled).  The returned string has the wake word stripped and
        is stripped of surrounding whitespace.
        """
        if not text:
            return None
        if self.push_to_talk:
            return text.strip()
        lower = text.lower()
        # Allow "Jarvis, do something" or "Jarvis do something"
        if self.wake_word:
            pattern = rf"^\s*{re.escape(self.wake_word)}[ ,.!?\s]*"
            if re.match(pattern, lower):
                command = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
                # If the user said *only* the wake word, ignore it.
                return command if command else None
            return None
        return text.strip()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _ensure_recorder(self):
        """Lazy-import and construct the RealtimeSTT recorder."""
        if self._recorder is not None:
            return self._recorder
        try:
            from RealtimeSTT import AudioToTextRecorder
        except ImportError as exc:
            raise RuntimeError(
                "RealtimeSTT is not installed. Run the installer or "
                "`pip install RealtimeSTT faster-whisper`."
            ) from exc

        logger.info(
            "Initializing RealtimeSTT (model=%s, language=%s, silence=%.2fs)",
            self.model, self.language, self.silence_threshold,
        )
        self._recorder = AudioToTextRecorder(
            model=self.model,
            language=self.language,
            # RealtimeSTT uses spinner + post_speech_silence for endpointing
            post_speech_silence=self.silence_threshold,
            # Print nothing during normal operation; JARVIS handles UI.
            print_transcript_time=False,
            silero_use_onnx=False,
        )
        return self._recorder

    def _text_callback(self, text: str) -> None:
        """Internal callback fired by the recorder for each final transcript."""
        if not text:
            return
        logger.debug("Raw transcript: %r", text)
        self.last_transcript = text
        command = self._extract_command(text)
        if command is None:
            logger.debug("Wake word '%s' not detected — ignoring.", self.wake_word)
            return
        logger.info("Command: %s", command)
        if self.on_transcript:
            try:
                self.on_transcript(command)
            except Exception:
                logger.exception("on_transcript callback raised")

    def _loop(self) -> None:
        """Background thread main loop — calls recorder.text() repeatedly."""
        recorder = self._ensure_recorder()
        try:
            while self._running:
                # .text() blocks until a full utterance is captured, then
                # calls the registered callback.  We pass our callback so
                # that we get every utterance.
                recorder.text(self._text_callback)
        except Exception:
            if self._running:
                logger.exception("STT listener loop crashed")

    def start(self) -> None:
        """Start listening in a background thread."""
        if self._running:
            logger.warning("STT listener already running.")
            return
        # Construct recorder (raises if deps missing).
        self._ensure_recorder()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="jarvis-stt",
                                        daemon=True)
        self._thread.start()
        logger.info("STT listener started (wake_word=%s, push_to_talk=%s)",
                    self.wake_word, self.push_to_talk)

    def stop(self) -> None:
        """Stop the listener and release resources."""
        self._running = False
        if self._recorder is not None:
            try:
                self._recorder.shutdown()
            except Exception:
                logger.debug("Recorder shutdown error (ignored)", exc_info=True)
            self._recorder = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("STT listener stopped.")

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #
    def listen_once(self, timeout: Optional[float] = None) -> Optional[str]:
        """Block until one utterance is recognised and return the command.

        This is a *synchronous* convenience method — useful for simple
        scripts or tests.  In the main loop we use :meth:`start` +
        ``on_transcript`` instead.

        Parameters
        ----------
        timeout : float, optional
            Not currently used by RealtimeSTT; kept for API symmetry.

        Returns
        -------
        str or None
            The recognised command text, or ``None`` if nothing heard.
        """
        recorder = self._ensure_recorder()
        text = recorder.text()  # type: ignore[func-returns-value]
        self.last_transcript = text or ""
        return self._extract_command(text or "")

    @property
    def is_running(self) -> bool:
        """True if the listener background loop is active."""
        return self._running

    def __repr__(self) -> str:
        return (f"<STTListener model={self.model!r} lang={self.language!r} "
                f"wake={self.wake_word!r} running={self._running}>")