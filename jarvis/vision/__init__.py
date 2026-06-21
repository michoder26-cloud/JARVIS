"""JARVIS vision module — the eyes that let AI see the screen.

Exposes :class:`ScreenVision`, which captures screenshots via pyautogui
and sends them to a multimodal LLM (minimax-m3 on Ollama Cloud) for
understanding, element location, and action verification.
"""

from __future__ import annotations

from .eyes import ScreenVision
from .loop import VisionActionLoop

__all__ = ["ScreenVision", "VisionActionLoop"]