"""LLM provider presets.

JARVIS talks to any OpenAI-compatible chat API. A provider preset fills in
the base URL and default models so users only need an API key:

* ``gemini`` — Google Gemini (free tier, good Thai). Key: ``GEMINI_API_KEY``
* ``groq``   — Groq (free tier, very fast). Key: ``GROQ_API_KEY``
* ``openai`` — OpenAI. Key: ``OPENAI_API_KEY``
* ``ollama`` — Ollama Cloud. Key: ``OLLAMA_API_KEY``

The provider is taken from ``JARVIS_PROVIDER`` or ``llm.provider`` in
config.yaml; with ``auto`` (the default) it is detected from whichever key is
set. ``JARVIS_API_KEY``, ``JARVIS_BASE_URL``, ``JARVIS_LLM_MODEL`` and
``JARVIS_FAST_MODEL`` override any preset value.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preset:
    base_url: str
    model: str
    fast_model: str
    key_envs: Tuple[str, ...]
    # Substrings used to pick a replacement when a default model is retired.
    prefer: Tuple[str, ...] = ()


PRESETS: Dict[str, Preset] = {
    "gemini": Preset(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-3.8-flash",
        fast_model="gemini-3.8-flash",
        key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        prefer=("flash",),
    ),
    "groq": Preset(
        base_url="https://api.groq.com/openai/v1",
        model="openai/gpt-oss-120b",
        fast_model="openai/gpt-oss-20b",
        key_envs=("GROQ_API_KEY",),
        prefer=("gpt-oss", "llama", "qwen"),
    ),
    "openai": Preset(
        base_url="https://api.openai.com/v1",
        model="gpt-5-mini",
        fast_model="gpt-5-mini",
        key_envs=("OPENAI_API_KEY",),
        prefer=("mini", "gpt"),
    ),
    "ollama": Preset(
        base_url="https://ollama.com/v1",
        model="glm-5.2",
        fast_model="glm-5.2",
        key_envs=("OLLAMA_API_KEY",),
    ),
}

DEFAULT_PROVIDER = "gemini"


@dataclass
class LLMSettings:
    provider: str
    base_url: str
    model: str
    fast_model: str
    api_key: Optional[str]
    key_env: str  # the variable the user should set, for error messages
    prefer: Tuple[str, ...] = ()


def resolve(config: Optional[dict] = None) -> LLMSettings:
    """Work out provider, URL, models and key from env vars and config."""
    llm = (config or {}).get("llm", {}) or {}
    env = os.environ.get

    name = (env("JARVIS_PROVIDER") or llm.get("provider") or "auto").strip().lower()
    if name not in PRESETS:
        name = _detect_provider() or DEFAULT_PROVIDER
    preset = PRESETS[name]

    api_key = env("JARVIS_API_KEY")
    if not api_key:
        for var in preset.key_envs:
            if env(var):
                api_key = env(var)
                break

    # A model in config.yaml only applies to the provider it was written for.
    same_provider = (llm.get("provider") or "").lower() in ("", "auto", name)
    cfg_model = llm.get("model") if same_provider else None
    cfg_fast = llm.get("fast_model") if same_provider else None
    model = env("JARVIS_LLM_MODEL") or cfg_model or preset.model
    return LLMSettings(
        provider=name,
        base_url=env("JARVIS_BASE_URL")
        or (env("OLLAMA_BASE_URL") if name == "ollama" else None)
        or (llm.get("base_url") if same_provider else None)
        or preset.base_url,
        model=model,
        fast_model=env("JARVIS_FAST_MODEL") or cfg_fast or (
            preset.fast_model if model == preset.model else model
        ),
        api_key=api_key,
        key_env=preset.key_envs[0],
        prefer=preset.prefer,
    )


def _detect_provider() -> Optional[str]:
    for name, preset in PRESETS.items():
        if any(os.environ.get(var) for var in preset.key_envs):
            return name
    return None


def pick_model(wanted: str, available: Sequence[str], prefer: Sequence[str]) -> str:
    """Return *wanted* if available, else the best match from *available*.

    Model names change often (e.g. a "flash" model gets a new version), so a
    default that has been retired is swapped for the newest similar model.
    """
    names = [a.split("/", 1)[1] if a.startswith("models/") else a for a in available]
    if not names or wanted in names:
        return wanted
    for hint in prefer:
        matches: List[str] = [
            n for n in names
            if hint in n and not any(x in n for x in ("embed", "image", "tts", "audio", "live", "vision"))
        ]
        if matches:
            return sorted(matches, key=_version_key, reverse=True)[0]
    return wanted


def _version_key(name: str) -> Tuple:
    """Sort key that puts higher version numbers and stable releases first."""
    import re

    nums = tuple(int(n) for n in re.findall(r"\d+", name))
    stable = not any(x in name for x in ("preview", "exp", "lite"))
    return (stable, nums)


def check_models(settings: LLMSettings, timeout: float = 10.0) -> LLMSettings:
    """Replace retired default models with available ones (best effort)."""
    if not settings.api_key or not settings.prefer:
        return settings
    try:
        from openai import OpenAI

        client = OpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=timeout)
        available = [m.id for m in client.models.list()]
    except Exception as exc:  # noqa: BLE001 — listing is optional
        logger.info("Could not list models (%s); using %s", exc, settings.model)
        return settings
    model = pick_model(settings.model, available, settings.prefer)
    fast = pick_model(settings.fast_model, available, settings.prefer)
    if (model, fast) != (settings.model, settings.fast_model):
        logger.warning("Model %s not available — using %s", settings.model, model)
    settings.model, settings.fast_model = model, fast
    return settings


# Name used by callers that also import other "resolve" helpers.
resolve_llm = resolve
