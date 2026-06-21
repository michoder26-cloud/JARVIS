"""JARVIS Brain — the LLM thinking module.

This package contains the AI brain of JARVIS:

* :mod:`jarvis.brain.prompts` — system prompt defining the JARVIS persona
* :mod:`jarvis.brain.llm`     — Ollama client with function-calling support
* :mod:`jarvis.brain.tools`   — tool/function schemas for automation actions
* :mod:`jarvis.brain.conversation` — conversation history manager
"""

from .llm import LLMClient
from .prompts import JARVIS_PROMPT
from .tools import ALL_TOOLS
from .conversation import ConversationHistory

__all__ = ["LLMClient", "JARVIS_PROMPT", "ALL_TOOLS", "ConversationHistory"]