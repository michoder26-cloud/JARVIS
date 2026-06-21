"""Speech-to-Text (STT) subpackage for JARVIS.

Provides continuous speech recognition with wake-word detection
using RealtimeSTT / faster-whisper.
"""

from jarvis.stt.listener import STTListener

__all__ = ["STTListener"]