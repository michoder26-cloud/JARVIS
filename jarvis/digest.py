"""Morning Digest / Daily Briefing for JARVIS.

Collects weather, time, and news data, sends them to the LLM with a
briefing prompt, speaks the result via TTS, and returns the briefing text.
"""

from __future__ import annotations

import logging
from typing import Optional

from jarvis.brain.llm import LLMClient
from jarvis.tts.speaker import TTSSpeaker

logger = logging.getLogger(__name__)

BRIEFING_PROMPT = (
    "You are giving a morning briefing. Summarize the weather, time, and "
    "news into a 2-3 sentence spoken briefing in the user's language. "
    "Be concise and natural. Don't say 'here is your briefing' — just "
    "start speaking."
)


def morning_briefing(
    llm: LLMClient,
    speaker: TTSSpeaker,
    location: str = "Bangkok",
) -> str:
    """Generate and speak a morning briefing.

    Collects:
      1. Weather via :func:`get_weather` from :mod:`jarvis.actions.system`
      2. Time/date via :func:`get_time` from :mod:`jarvis.actions.system`
      3. News headlines via :func:`get_news` from :mod:`jarvis.actions.news`
         (if available, else skipped)

    Sends collected data to the LLM with :data:`BRIEFING_PROMPT`, speaks the
    result via TTS, and returns the briefing text.
    """
    data_parts = []

    # 1. Weather
    try:
        from jarvis.actions.system import get_weather
        weather = get_weather(location)
        if weather.get("success"):
            data_parts.append(
                f"WEATHER: {weather.get('summary', '')} | "
                f"detail: {weather.get('detail', '')}"
            )
        else:
            data_parts.append(
                f"WEATHER: unavailable ({weather.get('error', 'unknown')})"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Weather collection failed: %s", exc)
        data_parts.append("WEATHER: unavailable")

    # 2. Time / date
    try:
        from jarvis.actions.system import get_time
        time_info = get_time()
        if time_info.get("success"):
            data_parts.append(
                f"TIME: {time_info.get('time', '')} | "
                f"DATE: {time_info.get('date', '')}"
            )
        else:
            data_parts.append(
                f"TIME: unavailable ({time_info.get('error', 'unknown')})"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Time collection failed: %s", exc)
        data_parts.append("TIME: unavailable")

    # 3. News (optional — skip if module unavailable)
    try:
        from jarvis.actions.news import get_news
        news = get_news(max_items=5)
        if news.get("success"):
            headlines = news.get("headlines", [])
            headlines_text = "; ".join(
                f"{h.get('title', '')} ({h.get('source', '')})"
                for h in headlines
            )
            data_parts.append(f"NEWS: {headlines_text}")
        else:
            data_parts.append("NEWS: unavailable")
    except Exception as exc:  # noqa: BLE001
        logger.info("News collection skipped/failed: %s", exc)
        data_parts.append("NEWS: unavailable")

    # Compose user payload for the LLM.
    payload = "\n".join(data_parts)
    messages = [
        {"role": "system", "content": BRIEFING_PROMPT},
        {"role": "user", "content": f"Current data:\n{payload}"},
    ]

    # Ask the LLM for the briefing.
    response = llm.chat(messages)
    briefing = (response.get("content") or "").strip()
    if not briefing:
        logger.warning("LLM returned empty briefing content.")
        briefing = "Good morning. I couldn't prepare a briefing right now."

    # Speak it.
    try:
        speaker.speak(briefing)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS failed during briefing: %s", exc)

    return briefing