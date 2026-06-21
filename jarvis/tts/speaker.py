"""Text-to-Speech speaker for JARVIS.

Uses Microsoft Edge TTS (``edge-tts``) — a free online TTS service that
requires no API key and supports Thai, English, German, and many other
languages with high-quality neural voices.

The module is async internally (edge-tts is asyncio-based) but exposes a
synchronous :meth:`TTSSpeaker.speak` that manages the event loop for
you, so callers from synchronous code need not worry about asyncio.
"""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
import wave
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Available voices, keyed by a short alias for easy selection.
VOICES: Dict[str, str] = {
    # Thai
    "th-female": "th-TH-PremwadeeNeural",
    "th-male": "th-TH-NiwatNeural",
    # English (US)
    "en-female": "en-US-AriaNeural",
    "en-male": "en-US-GuyNeural",
    # German
    "de-female": "de-DE-KatjaNeural",
    "de-male": "de-DE-ConradNeural",
}


class TTSSpeaker:
    """Speak text aloud using edge-tts.

    Parameters
    ----------
    voice : str
        Default voice name (e.g. ``th-TH-PremwadeeNeural``) or one of the
        aliases in :data:`VOICES`.
    rate : int
        Speech rate adjustment as a percentage: ``0`` = normal,
        ``+50`` = 50% faster, ``-20`` = 20% slower.
    volume : int
        Volume adjustment as a percentage: ``0`` = normal, ``+100`` = max,
        ``-50`` = quieter.
    """

    def __init__(
        self,
        voice: str = "th-TH-PremwadeeNeural",
        rate: int = 0,
        volume: int = 0,
    ) -> None:
        self.voice = VOICES.get(voice, voice)  # resolve alias if needed
        self.rate = rate
        self.volume = volume
        self._edge_tts = None  # lazy import

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _ensure_edge_tts(self):
        """Lazy-import edge_tts so the module loads without it installed."""
        if self._edge_tts is not None:
            return self._edge_tts
        try:
            import edge_tts  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "edge-tts is not installed. Run `pip install edge-tts`."
            ) from exc
        self._edge_tts = edge_tts
        return edge_tts

    def _fmt_param(self, value: int, prefix: str = "+") -> str:
        """Format an int as an edge-tts percentage string."""
        if value == 0:
            return "+0%"
        return f"{prefix}{value}%" if value > 0 else f"{value}%"

    # ------------------------------------------------------------------ #
    # Async core
    # ------------------------------------------------------------------ #
    async def _speak_async(self, text: str, voice: Optional[str]) -> bytes:
        """Synthesize *text* and return raw MP3 bytes.

        Parameters
        ----------
        text : str
            The text to speak.
        voice : str, optional
            Voice name override; falls back to :attr:`voice`.

        Returns
        -------
        bytes
            MP3 audio data.
        """
        edge_tts = self._ensure_edge_tts()
        voice = voice or self.voice
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=self._fmt_param(self.rate),
            volume=self._fmt_param(self.volume),
        )
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        return buffer.getvalue()

    async def _speak_and_play(self, text: str, voice: Optional[str]) -> None:
        """Synthesize and immediately play the audio."""
        mp3_data = await self._speak_async(text, voice)
        if not mp3_data:
            logger.warning("TTS produced no audio for: %r", text[:60])
            return
        await asyncio.get_event_loop().run_in_executor(
            None, _play_mp3, mp3_data
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def speak(self, text: str, voice: Optional[str] = None) -> None:
        """Speak *text* synchronously (manages the asyncio loop internally).

        Parameters
        ----------
        text : str
            Text to speak.
        voice : str, optional
            Voice name or alias override.  If None, uses the default
            configured voice.
        """
        if not text.strip():
            return
        logger.info("TTS: %r (voice=%s)", text[:80], voice or self.voice)
        try:
            self._run_async(self._speak_and_play(text, voice))
        except RuntimeError as exc:
            logger.error("TTS failed: %s", exc)

    async def speak_async(self, text: str, voice: Optional[str] = None) -> None:
        """Async speak — for callers already inside an event loop."""
        if not text.strip():
            return
        await self._speak_and_play(text, voice)

    def _run_async(self, coro):
        """Run *coro* to completion, creating a loop if needed."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # We're inside an existing loop (e.g. Jupyter).  Create a task
            # and block on it via run_until_complete on a fresh loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    @staticmethod
    def list_voices() -> Dict[str, str]:
        """Return the built-in voice alias → name mapping."""
        return dict(VOICES)

    @staticmethod
    async def fetch_available_voices() -> list:
        """Query edge-tts for *all* available voices (network call)."""
        import edge_tts  # type: ignore
        voices = await edge_tts.list_voices()
        return voices

    def __repr__(self) -> str:
        return (f"<TTSSpeaker voice={self.voice!r} rate={self.rate:+d}% "
                f"volume={self.volume:+d}%>")


# ---------------------------------------------------------------------- #
# Audio playback helper
# ---------------------------------------------------------------------- #
def _play_mp3(mp3_data: bytes) -> None:
    """Play MP3 bytes through the default audio output device.

    Tries ``sounddevice`` + numpy (decode via ffmpeg-free approach using
    the ``pydub``-less minimal path).  Falls back to writing a temp file
    and using an OS player if the preferred path is unavailable.
    """
    # Preferred path: sounddevice can't decode MP3 directly, so we
    # decode with a minimal subprocess ffmpeg if present, else fall back
    # to the ``subprocess`` + OS player approach.
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np  # type: ignore

        # Decode MP3 → PCM via ffmpeg if available.
        import shutil, subprocess, sys
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            proc = subprocess.run(
                [ffmpeg, "-i", "pipe:0", "-f", "s16le",
                 "-ar", "24000", "-ac", "1", "pipe:1"],
                input=mp3_data, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=True,
            )
            audio = np.frombuffer(proc.stdout, dtype=np.int16)
            sd.play(audio, samplerate=24000)
            sd.wait()
            return

        # No ffmpeg — try pydub if installed.
        try:
            from pydub import AudioSegment  # type: ignore
            from pydub.playback import play as pydub_play  # type: ignore
            seg = AudioSegment.from_mp3(io.BytesIO(mp3_data))
            pydub_play(seg)
            return
        except ImportError:
            pass
    except Exception:
        logger.debug("sounddevice playback failed, falling back", exc_info=True)

    # Fallback: write temp file and use OS player.
    import os, platform
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.write(mp3_data)
    tmp.close()
    try:
        if platform.system() == "Windows":
            os.startfile(tmp.name)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            import subprocess
            subprocess.Popen(["afplay", tmp.name])
        else:
            import subprocess, shutil
            player = shutil.which("mpg123") or shutil.which("aplay") or shutil.which("ffplay")
            if player:
                subprocess.Popen([player, tmp.name])
            else:
                logger.warning("No audio player found; wrote TTS to %s", tmp.name)
    except Exception:
        logger.error("Audio playback failed entirely.", exc_info=True)