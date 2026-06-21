"""Audio device helpers for JARVIS.

Provides discovery and testing of audio input/output devices using
``sounddevice`` (which wraps PortAudio on all platforms).

All functions degrade gracefully when no audio backend is available —
they return empty lists or ``None`` rather than raising, so the rest of
JARVIS can continue (e.g. in a headless CI container without a mic).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# sounddevice is optional at import time — we don't want to crash the
# whole package on a headless box.  Each function checks for it.
try:
    import sounddevice as sd
    import numpy as np
    _SD_AVAILABLE = True
except Exception as exc:  # pragma: no cover - environment dependent
    logger.warning("sounddevice unavailable (%s); audio helpers will be no-ops", exc)
    sd = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    _SD_AVAILABLE = False


def _ensure_sd() -> None:
    """Raise a helpful error if sounddevice could not be imported."""
    if not _SD_AVAILABLE:
        raise RuntimeError(
            "sounddevice is not available. On Linux install 'portaudio19-dev' "
            "and 'python3-sounddevice'; on Windows install with 'pip install sounddevice'."
        )


def list_input_devices() -> List[Dict[str, Any]]:
    """Return all available audio **input** (microphone) devices.

    Each entry is a dict with keys: ``index``, ``name``, ``hostapi``,
    ``max_input_channels``, ``default_samplerate``.

    Returns
    -------
    list[dict]
        List of input devices; empty list if none found or backend missing.
    """
    if not _SD_AVAILABLE:
        return []
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logger.error("Failed to query audio devices: %s", exc)
        return []

    result: List[Dict[str, Any]] = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append({
                "index": idx,
                "name": dev["name"],
                "hostapi": sd.query_hostapis(dev["hostapi"])["name"]
                if "hostapi" in dev else "unknown",
                "max_input_channels": dev["max_input_channels"],
                "default_samplerate": dev.get("default_samplerate", 0),
            })
    return result


def list_output_devices() -> List[Dict[str, Any]]:
    """Return all available audio **output** (speaker) devices.

    Each entry mirrors :func:`list_input_devices` but for outputs.

    Returns
    -------
    list[dict]
        List of output devices; empty list if none or backend missing.
    """
    if not _SD_AVAILABLE:
        return []
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logger.error("Failed to query audio devices: %s", exc)
        return []

    result: List[Dict[str, Any]] = []
    for idx, dev in enumerate(devices):
        if dev["max_output_channels"] > 0:
            result.append({
                "index": idx,
                "name": dev["name"],
                "hostapi": sd.query_hostapis(dev["hostapi"])["name"]
                if "hostapi" in dev else "unknown",
                "max_output_channels": dev["max_output_channels"],
                "default_samplerate": dev.get("default_samplerate", 0),
            })
    return result


def get_default_input_device() -> Optional[Dict[str, Any]]:
    """Return the default input device, or ``None`` if unavailable."""
    if not _SD_AVAILABLE:
        return None
    try:
        devices = sd.query_devices()
        default_idx = sd.default.device[0]
        if default_idx is None:
            return None
        dev = devices[default_idx]
        return {
            "index": default_idx,
            "name": dev["name"],
            "max_input_channels": dev["max_input_channels"],
            "default_samplerate": dev.get("default_samplerate", 0),
        }
    except Exception as exc:
        logger.debug("No default input device: %s", exc)
        return None


def get_default_output_device() -> Optional[Dict[str, Any]]:
    """Return the default output device, or ``None`` if unavailable."""
    if not _SD_AVAILABLE:
        return None
    try:
        devices = sd.query_devices()
        default_idx = sd.default.device[1]
        if default_idx is None:
            return None
        dev = devices[default_idx]
        return {
            "index": default_idx,
            "name": dev["name"],
            "max_output_channels": dev["max_output_channels"],
            "default_samplerate": dev.get("default_samplerate", 0),
        }
    except Exception as exc:
        logger.debug("No default output device: %s", exc)
        return None


def test_microphone(seconds: float = 1.0, samplerate: int = 16000) -> bool:
    """Record *seconds* of audio from the default mic and check for signal.

    Parameters
    ----------
    seconds : float
        Duration to record for the test.
    samplerate : int
        Sample rate to use (16 kHz is Whisper's native rate).

    Returns
    -------
    bool
        ``True`` if the microphone captured non-silent audio, ``False``
        if no mic, capture failed, or audio was all-zero.
    """
    if not _SD_AVAILABLE:
        logger.warning("sounddevice unavailable — cannot test microphone.")
        return False
    try:
        recording = sd.rec(int(seconds * samplerate), samplerate=samplerate,
                           channels=1, dtype="float32")
        sd.wait()
        if np is None:
            return True  # captured something but can't analyse
        peak = float(np.max(np.abs(recording)))
        logger.info("Mic test: peak amplitude %.4f over %.1fs", peak, seconds)
        return peak > 1e-4
    except Exception as exc:
        logger.error("Microphone test failed: %s", exc)
        return False


def print_audio_info() -> None:
    """Print a human-readable summary of all audio devices (for debugging)."""
    print("=== Audio Input Devices ===")
    for d in list_input_devices():
        print(f"  [{d['index']}] {d['name']} ({d.get('hostapi','?')}) "
              f"ch={d['max_input_channels']} sr={d['default_samplerate']}")
    print("\n=== Audio Output Devices ===")
    for d in list_output_devices():
        print(f"  [{d['index']}] {d['name']} ({d.get('hostapi','?')}) "
              f"ch={d['max_output_channels']} sr={d['default_samplerate']}")
    di = get_default_input_device()
    do = get_default_output_device()
    print(f"\nDefault input : {di['name'] if di else 'none'}")
    print(f"Default output: {do['name'] if do else 'none'}")