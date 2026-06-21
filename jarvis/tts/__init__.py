"""Text-to-Speech (TTS) subpackage for JARVIS.

Provides speech synthesis using Microsoft Edge TTS (free, no API key).
"""

from jarvis.tts.speaker import TTSSpeaker

__all__ = ["TTSSpeaker"]