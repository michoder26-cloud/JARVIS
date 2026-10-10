"""Tests for LLM provider selection."""

import pytest

from jarvis.brain import providers
from jarvis.brain.providers import pick_model, resolve

KEYS = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY",
        "JARVIS_PROVIDER", "JARVIS_API_KEY", "JARVIS_BASE_URL", "JARVIS_LLM_MODEL",
        "JARVIS_FAST_MODEL", "OLLAMA_BASE_URL"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def test_defaults_to_gemini_without_any_key():
    s = resolve()
    assert s.provider == "gemini" and s.api_key is None and s.key_env == "GEMINI_API_KEY"


def test_detects_provider_from_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    s = resolve()
    assert (s.provider, s.api_key, s.base_url) == ("groq", "g", "https://api.groq.com/openai/v1")


def test_gemini_key_and_preset(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    s = resolve({"llm": {"provider": "auto"}})
    assert s.provider == "gemini" and s.api_key == "k"
    assert "generativelanguage.googleapis.com" in s.base_url


def test_old_ollama_config_model_does_not_leak_into_other_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    s = resolve({"llm": {"provider": "ollama", "model": "glm-5.2"}})
    # Explicit provider in config wins; its model applies to it.
    assert s.provider == "ollama" and s.model == "glm-5.2"
    monkeypatch.setenv("JARVIS_PROVIDER", "gemini")
    s = resolve({"llm": {"provider": "ollama", "model": "glm-5.2"}})
    assert s.provider == "gemini" and s.model != "glm-5.2"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("JARVIS_API_KEY", "x")
    monkeypatch.setenv("JARVIS_BASE_URL", "http://local/v1")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "m1")
    s = resolve()
    assert (s.api_key, s.base_url, s.model, s.fast_model) == ("x", "http://local/v1", "m1", "m1")


def test_pick_model_replaces_retired_default():
    available = ["models/gemini-3.5-flash", "models/gemini-3.9-flash", "models/gemini-3.9-flash-lite",
                 "models/text-embedding-004", "models/gemini-3.9-flash-image"]
    assert pick_model("gemini-3.8-flash", available, ("flash",)) == "gemini-3.9-flash"
    assert pick_model("gemini-3.5-flash", available, ("flash",)) == "gemini-3.5-flash"
    assert pick_model("x", [], ("flash",)) == "x"


def test_check_models_tolerates_listing_failure(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("JARVIS_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens here
    s = providers.check_models(resolve(), timeout=0.5)
    assert s.model == providers.PRESETS["gemini"].model
