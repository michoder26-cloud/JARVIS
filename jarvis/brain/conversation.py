"""Conversation history manager for JARVIS.

Keeps a rolling window of messages (user, assistant, tool) so the LLM
has context without the history growing unbounded.  When the window
overflows, older messages are compacted into a short summary.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default max messages kept before compaction kicks in.
DEFAULT_MAX_MESSAGES = 20


class ConversationHistory:
    """Rolling conversation history with optional compaction.

    Parameters
    ----------
    system_prompt : str, optional
        The system prompt prepended to every ``messages`` output.  If
        None, no system message is included (the caller can add it).
    max_messages : int
        Maximum number of non-system messages to retain.  When
        exceeded, the oldest messages are summarized.
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_messages = max_messages
        self._messages: List[Dict[str, Any]] = []
        self._summary: str = ""

    # ------------------------------------------------------------------ #
    # Adding messages
    # ------------------------------------------------------------------ #
    def add_user(self, content: str) -> None:
        """Add a user message."""
        self._messages.append({"role": "user", "content": content})
        self._maybe_compact()

    def add_assistant(self, content: str) -> None:
        """Add an assistant text message."""
        self._messages.append({"role": "assistant", "content": content})
        self._maybe_compact()

    def add_assistant_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> None:
        """Add an assistant message that contains tool calls.

        ``tool_calls`` is the list as returned by Ollama, e.g.::

            [{"function": {"name": "open_browser", "arguments": {...}}}]
        """
        self._messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": tool_calls,
        })
        self._maybe_compact()

    def add_tool_result(self, tool_name: str, result: Any) -> None:
        """Add a tool result message.

        Ollama expects tool results with ``role='tool'``.
        """
        content = result if isinstance(result, str) else str(result)
        self._messages.append({
            "role": "tool",
            "name": tool_name,
            "content": content,
        })
        self._maybe_compact()

    def add_message(self, message: Dict[str, Any]) -> None:
        """Add a raw message dict (must have at least 'role' and 'content')."""
        self._messages.append(message)
        self._maybe_compact()

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def get_messages(self) -> List[Dict[str, Any]]:
        """Return messages formatted for the LLM, including system prompt.

        If a compaction summary exists, it's included as the first
        system-context message so the LLM retains context of old turns.
        """
        out: List[Dict[str, Any]] = []
        if self.system_prompt:
            sys_content = self.system_prompt
            if self._summary:
                sys_content += f"\n\n[Summary of earlier conversation]\n{self._summary}"
            out.append({"role": "system", "content": sys_content})
        elif self._summary:
            out.append({
                "role": "system",
                "content": f"[Summary of earlier conversation]\n{self._summary}",
            })
        out.extend(self._messages)
        return out

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """The raw internal message list (without system prompt)."""
        return list(self._messages)

    @property
    def summary(self) -> str:
        """The current compaction summary (empty if none)."""
        return self._summary

    # ------------------------------------------------------------------ #
    # Management
    # ------------------------------------------------------------------ #
    def clear(self) -> None:
        """Clear all messages and the summary."""
        self._messages.clear()
        self._summary = ""

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return (f"<ConversationHistory messages={len(self._messages)} "
                f"max={self.max_messages} summary={'yes' if self._summary else 'no'}>")

    # ------------------------------------------------------------------ #
    # Compaction
    # ------------------------------------------------------------------ #
    def _maybe_compact(self) -> None:
        """If over max_messages, compact the oldest messages into summary."""
        if len(self._messages) <= self.max_messages:
            return

        # Keep the most recent max_messages; summarize the rest.
        overflow = self._messages[:-self.max_messages]
        self._messages = self._messages[-self.max_messages:]

        summary_text = self._compact_messages(overflow)
        if self._summary:
            self._summary = f"{self._summary}\n{summary_text}"
        else:
            self._summary = summary_text

        logger.debug("Compacted %d old messages into summary.", len(overflow))

    @staticmethod
    def _compact_messages(messages: List[Dict[str, Any]]) -> str:
        """Create a short text summary of *messages*.

        This is a lightweight local summarizer that extracts the gist
        without calling the LLM.  Each message becomes a short line.
        """
        lines: List[str] = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if not content and "tool_calls" in msg:
                calls = msg["tool_calls"]
                names = [tc.get("function", {}).get("name", "?") for tc in calls]
                content = f"called tools: {', '.join(names)}"
            # Truncate long content.
            if len(content) > 120:
                content = content[:117] + "..."
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)