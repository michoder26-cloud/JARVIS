"""Async LLM helpers for the cloud web app.

Streaming translation, live lecture notes, lecture summaries and Q&A, built
on the same OpenAI-compatible endpoint the desktop assistant uses (Gemini,
Groq, OpenAI or Ollama — see :mod:`jarvis.brain.providers`).
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

Message = Dict[str, str]

# Language codes the UI offers, mapped to names the model understands.
LANGUAGE_NAMES: Dict[str, str] = {
    "th": "Thai",
    "en": "English",
    "de": "German",
    "ja": "Japanese",
    "zh": "Simplified Chinese",
    "ko": "Korean",
    "fr": "French",
    "es": "Spanish",
}

# Above this many characters the final summary is built in two passes.
SUMMARY_CHUNK_CHARS = 15_000


def language_name(code: str) -> str:
    """Return the English name for a language code (the code itself if unknown)."""
    return LANGUAGE_NAMES.get((code or "").split("-")[0].lower(), code or "English")


TRANSLATE_PROMPT = """\
You are a professional real-time interpreter. Translate the user's text into {target}.
Rules:
- Output ONLY the translation. No quotes, notes, or explanations.
- The text comes from live speech recognition and may contain recognition errors; translate the intended meaning.
- Keep names, numbers and units exact. For technical terms usually kept in English, add the English term in parentheses once.
- If the text is already in {target}, return it unchanged.
{context}"""

NOTES_PROMPT = """\
You take live notes for a student who is listening to a lecture.
Write the notes in {lang}, in Markdown:
- Use "## " headings for each topic the teacher covers, in order.
- Under each heading, short bullet points: key ideas, definitions, formulas, examples.
- Mark anything the teacher stresses as important, as an exam hint, homework or a deadline with "⚠️".
- The transcript comes from speech recognition and may contain errors; infer the intended words from context.
- Be concise. Never invent content that was not said.
You will receive the current notes and the newest part of the transcript.
Return the FULL updated notes only — no preamble."""

SUMMARY_PROMPT = """\
You turn a lecture transcript into study notes. Write in {lang}, in Markdown, with these sections:
# <a short title for the lecture>
## สรุปภาพรวม / Overview — one paragraph.
## Key points — bullets in the order they were taught.
## Terms & definitions — term: definition.
## Examples & formulas — if any were given.
## ⚠️ Exam hints, homework & deadlines — anything the teacher stressed; write "none mentioned" if there were none.
## Review questions — 3 to 5 questions with short answers.
Translate the section headings into {lang}. The transcript comes from speech
recognition and may contain errors; infer the intended words. Never invent content."""

CHUNK_PROMPT = """\
Summarise this part of a lecture transcript into detailed bullet-point notes in {lang}.
Keep every definition, formula, example, exam hint and deadline. No preamble."""

ANSWER_PROMPT = """\
You help a student understand a lecture. Answer their question in {lang} using the
lecture transcript and notes below. If the lecture did not cover it, say so, then
give a short general answer clearly marked as not from the lecture.

# Notes
{notes}

# Transcript
{transcript}"""


class AIService:
    """Thin async wrapper around an OpenAI-compatible chat endpoint.

    Works with any provider in :mod:`jarvis.brain.providers`.

    Parameters
    ----------
    model : str
        Model used for notes, summaries and answers.
    fast_model : str, optional
        Model used for live translation (latency matters most there).
        Defaults to *model*.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        fast_model: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.fast_model = fast_model or model
        self.base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._client = None

    @property
    def configured(self) -> bool:
        """True when an API key is available."""
        return bool(self._api_key)

    def _ensure_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self._api_key or "missing-key",
                timeout=self._timeout,
            )
        return self._client

    async def stream(
        self,
        messages: Sequence[Message],
        model: Optional[str] = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """Yield content deltas for a chat completion."""
        client = self._ensure_client()
        response = await client.chat.completions.create(
            model=model or self.model,
            messages=list(messages),
            temperature=temperature,
            stream=True,
        )
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def complete(
        self,
        messages: Sequence[Message],
        model: Optional[str] = None,
        temperature: float = 0.3,
    ) -> str:
        """Return the full text of a chat completion."""
        parts = [d async for d in self.stream(messages, model, temperature)]
        return "".join(parts).strip()

    # ------------------------------------------------------------------ #
    # Task-specific helpers
    # ------------------------------------------------------------------ #
    def translate(
        self, text: str, target: str, context: Sequence[str] = ()
    ) -> AsyncIterator[str]:
        """Stream a translation of *text* into language code *target*."""
        ctx = ""
        if context:
            joined = "\n".join(context)
            ctx = f"\nEarlier sentences, for context only (do not translate them):\n{joined}"
        messages = [
            {
                "role": "system",
                "content": TRANSLATE_PROMPT.format(target=language_name(target), context=ctx),
            },
            {"role": "user", "content": text},
        ]
        return self.stream(messages, model=self.fast_model, temperature=0.1)

    def update_notes(self, notes: str, new_transcript: str, lang: str) -> AsyncIterator[str]:
        """Stream the full updated live notes."""
        messages = [
            {"role": "system", "content": NOTES_PROMPT.format(lang=language_name(lang))},
            {
                "role": "user",
                "content": (
                    f"# Current notes\n{notes or '(empty)'}\n\n"
                    f"# Newest transcript\n{new_transcript}"
                ),
            },
        ]
        return self.stream(messages, temperature=0.2)

    async def summarize(self, transcript: str, lang: str) -> AsyncIterator[str]:
        """Stream final study notes for a whole lecture.

        Long transcripts are first condensed chunk by chunk so they fit the
        model's context window.
        """
        source = transcript
        if len(transcript) > SUMMARY_CHUNK_CHARS:
            chunk_notes: List[str] = []
            for i, chunk in enumerate(_chunks(transcript, SUMMARY_CHUNK_CHARS), 1):
                notes = await self.complete(
                    [
                        {"role": "system", "content": CHUNK_PROMPT.format(lang=language_name(lang))},
                        {"role": "user", "content": chunk},
                    ],
                    temperature=0.2,
                )
                chunk_notes.append(f"### Part {i}\n{notes}")
            source = "\n\n".join(chunk_notes)
        messages = [
            {"role": "system", "content": SUMMARY_PROMPT.format(lang=language_name(lang))},
            {"role": "user", "content": source},
        ]
        async for delta in self.stream(messages, temperature=0.2):
            yield delta

    def answer(self, question: str, transcript: str, notes: str, lang: str) -> AsyncIterator[str]:
        """Stream an answer to a question about the lecture."""
        # Keep the most recent part of very long lectures.
        transcript = transcript[-40_000:]
        messages = [
            {
                "role": "system",
                "content": ANSWER_PROMPT.format(
                    lang=language_name(lang),
                    notes=notes or "(none yet)",
                    transcript=transcript or "(empty)",
                ),
            },
            {"role": "user", "content": question},
        ]
        return self.stream(messages, temperature=0.3)


def _chunks(text: str, size: int) -> List[str]:
    """Split *text* into pieces of about *size* characters on whitespace."""
    out: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + size // 2, end)
            if cut == -1:
                cut = text.rfind("\n", start + size // 2, end)
            if cut != -1:
                end = cut
        out.append(text[start:end].strip())
        start = end
    return [c for c in out if c]
